"""Browser-safe gateway to the existing deployed customer-service agent.

Run one worker locally with::

    uv run uvicorn app.chat_gateway:app --host 127.0.0.1 --port 8081

The Next.js server proxies these endpoints on the browser's own origin. Google
credentials stay in this process. A signed, HttpOnly visitor cookie owns the
managed sessions; this is anonymous browser continuity, not account login.

Production requires GATEWAY_ENV=production, GATEWAY_COOKIE_SECRET (32+ chars)
and GATEWAY_ALLOWED_ORIGINS (comma-separated exact HTTPS origins). The turn
guard is process-local: run a single worker/instance until a shared lease store
and customer authentication are added for a public, horizontally scaled launch.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
COOKIE_NAME = "tarnfield_visitor"
COOKIE_TTL_SECONDS = 365 * 24 * 60 * 60
SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
VISITOR_ID = re.compile(r"^[a-f0-9]{32}$")
# A 6,000-character message may use six JSON bytes per escaped character.
# Leave room for the conversation ID and object envelope as well.
MAX_BODY_BYTES = 40_000
MAX_MESSAGE_CHARS = 6_000
MAX_ANSWER_CHARS = 64_000
TITLE_KEY = "ui:conversation_title"
TITLE_INITIALIZED_KEY = "ui:conversation_title_initialized"
DOCUMENT_TITLES = {
    "tarnfield_returns_policy.pdf": "Returns & exchanges policy",
    "tarnfield_product_catalogue.pdf": "Product catalogue",
}
TOOL_LABELS = {"preload_memory": "Recalling useful details"}


def _development_secret() -> str:
    """Keep browser continuity across development server restarts."""
    path = ROOT / ".sessions" / "chat_gateway_cookie_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    secret = secrets.token_hex(32)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(secret)
    return secret


@dataclass(frozen=True)
class GatewaySettings:
    cookie_secret: str
    allowed_origins: frozenset[str]
    production: bool = False
    api_timeout: float = 30
    turn_timeout: float = 120

    @classmethod
    def from_env(cls) -> GatewaySettings:
        production = os.getenv("GATEWAY_ENV", "development") == "production"
        secret = os.getenv("GATEWAY_COOKIE_SECRET", "")
        origins = os.getenv("GATEWAY_ALLOWED_ORIGINS", "")
        if production and (len(secret) < 32 or not origins):
            raise RuntimeError(
                "Production needs GATEWAY_COOKIE_SECRET (32+ characters) and "
                "explicit GATEWAY_ALLOWED_ORIGINS."
            )
        allowed = frozenset(
            value.strip().rstrip("/")
            for value in (origins or "http://127.0.0.1:3010").split(",")
            if value.strip()
        )
        for origin in allowed:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
                or (production and parsed.scheme != "https")
            ):
                raise RuntimeError(
                    "GATEWAY_ALLOWED_ORIGINS must contain exact origins."
                )
        return cls(
            cookie_secret=secret or _development_secret(),
            allowed_origins=allowed,
            production=production,
            api_timeout=float(os.getenv("GATEWAY_API_TIMEOUT_SECONDS", "30")),
            turn_timeout=float(os.getenv("GATEWAY_TURN_TIMEOUT_SECONDS", "120")),
        )


class GatewayError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        retryable: bool = False,
        metadata: dict[str, Any] | None = None,
    ):
        self.status = status
        self.message = message
        self.retryable = retryable
        self.metadata = metadata or {}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    conversationId: str | None = Field(default=None, pattern=SESSION_ID.pattern)

    @field_validator("message")
    @classmethod
    def strip_message(cls, message: str) -> str:
        if not message.strip():
            raise ValueError("Message cannot be empty.")
        return message.strip()


class AgentBackend(Protocol):
    async def create_session(
        self, user_id: str, title: str, *, title_initialized: bool = True
    ) -> dict[str, Any]: ...

    async def set_conversation_title(
        self, user_id: str, session_id: str, title: str
    ) -> dict[str, Any] | None: ...

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]: ...

    async def get_session(
        self, user_id: str, session_id: str, *, include_events: bool = True
    ) -> dict[str, Any] | None: ...

    def stream_query(
        self, user_id: str, session_id: str, message: str
    ) -> AsyncIterator[dict[str, Any]]: ...


class ManagedSessionBackend:
    """Use inspected Agent Platform and ADK managed-session APIs."""

    def __init__(self) -> None:
        self._session_service: Any = None
        self._runtime_name = ""

    def _sessions(self) -> Any:
        if self._session_service is None:
            from google.adk.sessions.vertex_ai_session_service import (
                VertexAiSessionService,
            )

            self._runtime_name = os.getenv("DEPLOYED_AGENT_RUNTIME_ID", "")
            if not self._runtime_name:
                metadata = json.loads((ROOT / "deployment_metadata.json").read_text())
                self._runtime_name = metadata["remote_agent_runtime_id"]
            match = re.fullmatch(
                r"projects/([^/]+)/locations/([^/]+)/reasoningEngines/([^/]+)",
                self._runtime_name,
            )
            if not match:
                raise RuntimeError("Invalid deployed Agent Runtime resource name.")
            project, location, runtime_id = match.groups()
            self._session_service = VertexAiSessionService(
                project=project, location=location, agent_engine_id=runtime_id
            )
        return self._session_service

    async def create_session(
        self, user_id: str, title: str, *, title_initialized: bool = True
    ) -> dict[str, Any]:
        service = self._sessions()
        session = await service.create_session(
            app_name=self._runtime_name,
            user_id=user_id,
            state={TITLE_KEY: title, TITLE_INITIALIZED_KEY: title_initialized},
        )
        return session.model_dump(mode="json")

    async def set_conversation_title(
        self, user_id: str, session_id: str, title: str
    ) -> dict[str, Any] | None:
        from google.adk.events import Event, EventActions
        from google.adk.sessions.base_session_service import GetSessionConfig

        service = self._sessions()
        try:
            session = await service.get_session(
                app_name=self._runtime_name,
                user_id=user_id,
                session_id=session_id,
                config=GetSessionConfig(num_recent_events=0),
            )
        except ValueError:
            return None
        if session is None or session.user_id != user_id:
            return None
        if _needs_title(session.state):
            # A state-only, non-user event updates the managed title without
            # inserting a message or invoking/changing the existing agent.
            metadata = Event(
                author="chat_gateway",
                invocation_id=f"gateway-title-{secrets.token_hex(8)}",
                actions=EventActions(
                    state_delta={
                        TITLE_KEY: title,
                        TITLE_INITIALIZED_KEY: True,
                    }
                ),
            )
            await service.append_event(session=session, event=metadata)
        return session.model_dump(mode="json")

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        service = self._sessions()
        result = await service.list_sessions(
            app_name=self._runtime_name, user_id=user_id
        )
        # Recheck ownership rather than relying solely on a remote filter.
        return [
            session.model_dump(mode="json")
            for session in result.sessions
            if session.user_id == user_id
        ]

    async def get_session(
        self, user_id: str, session_id: str, *, include_events: bool = True
    ) -> dict[str, Any] | None:
        from google.adk.sessions.base_session_service import GetSessionConfig

        service = self._sessions()
        try:
            session = await service.get_session(
                app_name=self._runtime_name,
                user_id=user_id,
                session_id=session_id,
                config=None
                if include_events
                else GetSessionConfig(num_recent_events=0),
            )
        except ValueError:
            # The managed API raises ValueError for another user's session.
            return None
        if session is None or session.user_id != user_id:
            return None
        return session.model_dump(mode="json")


def _signed_visitor(settings: GatewaySettings, visitor: str, issued: int) -> str:
    payload = f"{visitor}.{issued}"
    signature = hmac.new(
        settings.cookie_secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def _visitor(request: Request, settings: GatewaySettings) -> tuple[str, str | None]:
    name = f"__Host-{COOKIE_NAME}" if settings.production else COOKIE_NAME
    cookie = request.cookies.get(name, "")
    try:
        visitor, issued, signature = cookie.split(".")
        issued_time = int(issued)
        expected = _signed_visitor(settings, visitor, issued_time).rsplit(".", 1)[1]
        if (
            VISITOR_ID.fullmatch(visitor)
            and 0 <= time.time() - issued_time < COOKIE_TTL_SECONDS
            and hmac.compare_digest(signature, expected)
        ):
            return f"visitor-{visitor}", None
    except (TypeError, ValueError):
        pass
    visitor = secrets.token_hex(16)
    token = _signed_visitor(settings, visitor, int(time.time()))
    return f"visitor-{visitor}", token


def _set_cookie(response: Any, token: str | None, settings: GatewaySettings) -> Any:
    if token:
        response.set_cookie(
            f"__Host-{COOKIE_NAME}" if settings.production else COOKIE_NAME,
            token,
            max_age=COOKIE_TTL_SECONDS,
            httponly=True,
            secure=settings.production,
            samesite="strict",
            path="/",
        )
    response.headers["Cache-Control"] = "no-store"
    return response


def _check_origin(request: Request, settings: GatewaySettings) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise GatewayError(403, "Open support from the Tarnfield website.")
    if (origin and origin not in settings.allowed_origins) or (
        request.method == "POST" and not origin
    ):
        raise GatewayError(403, "Open support from the Tarnfield website.")


async def _read_json(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise GatewayError(415, "Send your message as JSON.")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY_BYTES:
            raise GatewayError(413, "Your message is too long. Please shorten it.")
        raw.extend(chunk)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise GatewayError(
            400, "We couldn't read that message. Please try again."
        ) from error
    if not isinstance(body, dict):
        raise GatewayError(400, "We couldn't read that message. Please try again.")
    return body


class LocalPolicyBackend(ManagedSessionBackend):
    """Run current source against managed sessions + Memory Bank, not old cloud code.

    CHAT_STATE_BACKEND=local provides SQLite sessions and in-memory recall for
    isolated development. Default managed mode reuses the configured runtime's
    state services, but never invokes its legacy deployed agent.
    """

    def __init__(self) -> None:
        super().__init__()
        self._runner = None

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.close()

    def _sessions(self) -> Any:
        if os.getenv("CHAT_STATE_BACKEND", "managed") == "local":
            if self._session_service is None:
                from app.app_utils.services import get_session_service

                self._session_service = get_session_service()
                self._runtime_name = "app"
            return self._session_service
        return super()._sessions()

    async def stream_query(self, user_id: str, session_id: str, message: str):
        from google.adk.agents.run_config import RunConfig, StreamingMode
        from google.adk.runners import Runner
        from google.genai import types

        if self._runner is None:
            from app.agent import app as policy_app

            sessions = self._sessions()
            if os.getenv("CHAT_STATE_BACKEND", "managed") == "local":
                from app.app_utils.services import get_memory_service

                memory = get_memory_service()
            else:
                from google.adk.memory.vertex_ai_memory_bank_service import (
                    VertexAiMemoryBankService,
                )

                parts = self._runtime_name.split("/")
                memory = VertexAiMemoryBankService(
                    project=parts[1], location=parts[3], agent_engine_id=parts[5]
                )
            self._runner = Runner(
                app=policy_app, session_service=sessions, memory_service=memory
            )
        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(
                role="user", parts=[types.Part.from_text(text=message)]
            ),
            # Validate the full structured answer before exposing customer text.
            run_config=RunConfig(streaming_mode=StreamingMode.NONE),
        ):
            yield event.model_dump(mode="json", exclude_none=True)


def _parts(event: dict[str, Any]) -> list[dict[str, Any]]:
    content = event.get("content")
    if not isinstance(content, dict):
        return []
    parts = content.get("parts")
    if not isinstance(parts, list):
        return []
    return [part for part in parts if isinstance(part, dict)]


def _visible_text(event: dict[str, Any]) -> str:
    parts = _parts(event)
    # Text accompanying a tool call is internal work, not a completed reply.
    if any(
        part.get("function_call") or part.get("function_response") for part in parts
    ):
        return ""
    return "".join(
        part["text"]
        for part in parts
        if isinstance(part.get("text"), str) and not part.get("thought")
    )


def _sources(event: dict[str, Any]) -> list[dict[str, str]]:
    """Accept only citation metadata validated by the policy callback."""
    delta = (event.get("actions") or {}).get("state_delta") or {}
    citations = delta.get("policy:citations") or []
    found = []
    for item in citations if isinstance(citations, list) else []:
        if not isinstance(item, dict) or item.get("document") not in DOCUMENT_TITLES:
            continue
        if not isinstance(item.get("excerpt"), str) or not item.get("passage_id"):
            continue
        found.append(
            {
                "title": DOCUMENT_TITLES[item["document"]],
                "document": item["document"],
                "excerpt": item["excerpt"][:1600],
                "passageId": str(item["passage_id"]),
            }
        )
    return found


def _history(session: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    evidence_by_turn: dict[str, dict[str, dict[str, str]]] = {}
    for event in session.get("events") or []:
        invocation = event.get("invocation_id", "")
        evidence = evidence_by_turn.setdefault(invocation, {})
        evidence.update({source["document"]: source for source in _sources(event)})
        author = event.get("author")
        if author not in ("user", "customer_service") or event.get("partial"):
            continue
        text = _visible_text(event)
        if not text:
            continue
        message = {
            "id": event.get("id") or f"message-{len(messages)}",
            "role": "user" if author == "user" else "assistant",
            "content": text,
        }
        if author == "customer_service" and evidence:
            message["sources"] = list(evidence.values())
        messages.append(message)
    return messages


def _sse(kind: str, payload: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _needs_title(state: dict[str, Any]) -> bool:
    return state.get(TITLE_INITIALIZED_KEY) is not True and state.get(TITLE_KEY) in (
        None,
        "",
        "New conversation",
        "Conversation",
    )


def _message_title(message: str) -> str:
    return " ".join(message.split())[:60]


def _conversation_title(session: dict[str, Any]) -> str:
    return str((session.get("state") or {}).get(TITLE_KEY) or "Conversation")[:80]


def create_app(
    *, settings: GatewaySettings | None = None, backend: AgentBackend | None = None
) -> FastAPI:
    settings = settings or GatewaySettings.from_env()
    backend = backend or LocalPolicyBackend()
    active_visitors: set[str] = set()
    active_conversations: dict[str, str] = {}
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if close := getattr(backend, "close", None):
            await close()

    app = FastAPI(title="Tarnfield customer chat gateway", lifespan=lifespan)
    app.state.active_visitors = active_visitors
    app.state.active_conversations = active_conversations
    app.state.turn_tasks = tasks

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, error: GatewayError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "message": error.message,
                    "retryable": error.retryable,
                    **error.metadata,
                }
            },
            status_code=error.status,
            headers={"Cache-Control": "no-store"},
        )

    def busy(user_id: str) -> GatewayError:
        metadata: dict[str, Any] = {"code": "conversation_busy", "active": True}
        if conversation_id := active_conversations.get(user_id):
            metadata["conversationId"] = conversation_id
        return GatewayError(
            409,
            "Please wait for your current support task to finish.",
            metadata=metadata,
        )

    async def cloud_call(
        awaitable: Any,
        *,
        failure_message: str = "Support is temporarily unavailable. Please try again shortly.",
    ) -> Any:
        try:
            async with asyncio.timeout(settings.api_timeout):
                return await awaitable
        except GatewayError:
            raise
        except Exception as error:
            logger.warning("Managed session request failed (%s)", type(error).__name__)
            raise GatewayError(503, failure_message, True) from error

    async def created_session(
        user_id: str, title: str, *, initialized: bool = True
    ) -> dict[str, Any]:
        message = "We couldn't create your conversation. Please try again."
        session = await cloud_call(
            backend.create_session(user_id, title, title_initialized=initialized),
            failure_message=message,
        )
        if (
            not isinstance(session, dict)
            or not isinstance(session.get("id"), str)
            or not SESSION_ID.fullmatch(session["id"])
            or session.get("user_id") != user_id
        ):
            logger.warning("Managed session creation returned invalid session metadata")
            raise GatewayError(503, message, True)
        return session

    async def owned_session(
        user_id: str, conversation_id: str, *, events: bool
    ) -> dict:
        if not SESSION_ID.fullmatch(conversation_id):
            raise GatewayError(
                404, "This conversation isn't available in this browser."
            )
        session = await cloud_call(
            backend.get_session(user_id, conversation_id, include_events=events)
        )
        if not session or session.get("user_id") != user_id:
            raise GatewayError(
                404, "This conversation isn't available in this browser."
            )
        return session

    @app.get("/health", include_in_schema=False)
    @app.get("/api/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/bootstrap", include_in_schema=False)
    async def bootstrap(request: Request) -> JSONResponse:
        _check_origin(request, settings)
        _, token = _visitor(request, settings)
        return _set_cookie(JSONResponse({"ready": True}), token, settings)

    @app.get("/api/conversations")
    async def conversations(request: Request) -> JSONResponse:
        _check_origin(request, settings)
        user_id, token = _visitor(request, settings)
        sessions = await cloud_call(backend.list_sessions(user_id))
        sessions = sorted(
            (session for session in sessions if session.get("user_id") == user_id),
            key=lambda session: session.get("last_update_time") or 0,
            reverse=True,
        )[:50]
        result = [
            {
                "id": session["id"],
                "title": _conversation_title(session),
                "active": user_id in active_visitors,
                "inFlight": active_conversations.get(user_id) == session["id"],
                "updatedAt": datetime.fromtimestamp(
                    session.get("last_update_time") or 0, UTC
                ).isoformat(),
            }
            for session in sessions
        ]
        return _set_cookie(
            JSONResponse(
                {
                    "conversations": result,
                    "active": user_id in active_visitors,
                    "activeConversationId": active_conversations.get(user_id),
                }
            ),
            token,
            settings,
        )

    @app.get("/api/conversations/{conversation_id}")
    async def conversation(request: Request, conversation_id: str) -> JSONResponse:
        _check_origin(request, settings)
        user_id, token = _visitor(request, settings)
        session = await owned_session(user_id, conversation_id, events=True)
        return _set_cookie(
            JSONResponse(
                {
                    "conversationId": conversation_id,
                    "title": _conversation_title(session),
                    "active": user_id in active_visitors,
                    "inFlight": active_conversations.get(user_id) == conversation_id,
                    "messages": _history(session),
                }
            ),
            token,
            settings,
        )

    @app.post("/api/conversations")
    async def new_conversation(request: Request) -> JSONResponse:
        _check_origin(request, settings)
        if await _read_json(request):
            raise GatewayError(400, "Create a conversation with an empty JSON object.")
        user_id, token = _visitor(request, settings)
        if user_id in active_visitors:
            raise busy(user_id)
        active_visitors.add(user_id)
        try:
            session = await created_session(
                user_id, "New conversation", initialized=False
            )
        finally:
            active_visitors.discard(user_id)
        return _set_cookie(
            JSONResponse(
                {
                    "conversationId": session["id"],
                    "title": _conversation_title(session),
                },
                status_code=201,
            ),
            token,
            settings,
        )

    @app.post("/api/chat")
    async def chat(request: Request) -> StreamingResponse:
        _check_origin(request, settings)
        try:
            body = ChatRequest.model_validate(await _read_json(request))
        except ValidationError as error:
            raise GatewayError(
                400, "Enter a message of up to 6,000 characters."
            ) from error
        user_id, token = _visitor(request, settings)
        if user_id in active_visitors:
            raise busy(user_id)
        active_visitors.add(user_id)
        try:
            if body.conversationId:
                session = await owned_session(
                    user_id, body.conversationId, events=False
                )
                if _needs_title(session.get("state") or {}):
                    session = await cloud_call(
                        backend.set_conversation_title(
                            user_id, body.conversationId, _message_title(body.message)
                        ),
                        failure_message="We couldn't save your conversation yet. Your message wasn't sent. Please try again.",
                    )
                    if not session or session.get("user_id") != user_id:
                        raise GatewayError(
                            404, "This conversation isn't available in this browser."
                        )
            else:
                session = await created_session(user_id, _message_title(body.message))
            conversation_id = session["id"]
            active_conversations[user_id] = conversation_id
        except BaseException:
            active_visitors.discard(user_id)
            raise

        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def produce() -> None:
            answer = ""
            completed_answer = False
            sources: dict[str, dict[str, str]] = {}
            last_status = ""
            try:
                queue.put_nowait(
                    _sse(
                        "session",
                        {
                            "conversationId": conversation_id,
                            "title": _conversation_title(session),
                        },
                    )
                )
                async with asyncio.timeout(settings.turn_timeout):
                    async for event in backend.stream_query(
                        user_id, conversation_id, body.message
                    ):
                        if event.get("error_code") or event.get("error_message"):
                            raise RuntimeError(
                                "Agent Runtime reported an incomplete turn."
                            )
                        for part in _parts(event):
                            tool = (part.get("function_call") or {}).get("name")
                            label = TOOL_LABELS.get(tool)
                            if label and label != last_status:
                                last_status = label
                                queue.put_nowait(_sse("status", {"label": label}))
                        changed = False
                        for source in _sources(event):
                            if source["document"] not in sources:
                                sources[source["document"]] = source
                                changed = True
                        if changed:
                            queue.put_nowait(
                                _sse("sources", {"sources": list(sources.values())})
                            )
                        if event.get("author") != "customer_service":
                            continue
                        if text := _visible_text(event):
                            mode: Literal["append", "replace"] = (
                                "append" if event.get("partial") else "replace"
                            )
                            completed_answer = mode == "replace"
                            answer = answer + text if mode == "append" else text
                            if len(answer) > MAX_ANSWER_CHARS:
                                raise RuntimeError("Answer exceeded the output limit.")
                            queue.put_nowait(_sse("text", {"text": text, "mode": mode}))
                if not answer.strip() or not completed_answer:
                    raise RuntimeError("No customer-facing reply was received.")
                queue.put_nowait(_sse("done", {}))
            except TimeoutError:
                queue.put_nowait(
                    _sse(
                        "error",
                        {
                            "message": "This reply took too long. Reopen the conversation to recover any saved answer, then try again.",
                            "retryable": False,
                        },
                    )
                )
            except Exception as error:
                logger.warning("Customer chat turn failed (%s)", type(error).__name__)
                queue.put_nowait(
                    _sse(
                        "error",
                        {
                            "message": "We couldn't finish this reply. Reopen the conversation to recover any saved answer, then try again.",
                            "retryable": False,
                        },
                    )
                )
            finally:
                active_visitors.discard(user_id)
                active_conversations.pop(user_id, None)
                queue.put_nowait(None)

        # A browser disconnect stops delivery, not the in-flight agent workflow.
        # Retain its guard until completion/timeout to prevent duplicate requests.
        task = asyncio.create_task(produce())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

        async def delivery() -> AsyncIterator[str]:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    return
                yield item

        return _set_cookie(
            StreamingResponse(
                delivery(),
                media_type="text/event-stream",
                headers={
                    "X-Accel-Buffering": "no",
                    "X-Content-Type-Options": "nosniff",
                },
            ),
            token,
            settings,
        )

    return app


app = create_app()
