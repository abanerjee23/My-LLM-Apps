"""Gateway contract and isolation checks; these never call a model or Google API."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.chat_gateway import (
    COOKIE_NAME,
    TITLE_INITIALIZED_KEY,
    TITLE_KEY,
    GatewayError,
    GatewaySettings,
    ManagedSessionBackend,
    _signed_visitor,
    create_app,
)

ORIGIN = "http://localhost:3000"
HEADERS = {"Origin": ORIGIN}
SECRET = "contract-test-secret-with-at-least-32-characters"


def event(author: str, text: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "id": f"event-{author}",
        "author": author,
        "invocation_id": "turn-1",
        "content": {"parts": [{"text": text}]},
        **extra,
    }


class FakeBackend:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.users: list[str] = []
        self.queries: list[tuple[str, str, str]] = []
        self.events = [event("customer_service", "Your support reply.")]
        self.failure: Exception | None = None
        self.creation_failure: Exception | None = None
        self.title_failure: Exception | None = None
        self.title_updates: list[tuple[str, str, str]] = []
        self.creation_block: asyncio.Event | None = None
        self.block: asyncio.Event | None = None

    async def create_session(
        self, user_id: str, title: str, *, title_initialized: bool = True
    ) -> dict[str, Any]:
        self.users.append(user_id)
        if self.creation_block is not None:
            await self.creation_block.wait()
        if self.creation_failure:
            raise self.creation_failure
        session = {
            "id": f"session-{len(self.sessions) + 1}",
            "user_id": user_id,
            "state": {TITLE_KEY: title, TITLE_INITIALIZED_KEY: title_initialized},
            "last_update_time": 1_759_300_000,
            "events": [],
        }
        self.sessions[session["id"]] = session
        return session

    async def set_conversation_title(
        self, user_id: str, session_id: str, title: str
    ) -> dict[str, Any] | None:
        self.title_updates.append((user_id, session_id, title))
        if self.title_failure:
            raise self.title_failure
        session = self.sessions.get(session_id)
        if not session or session["user_id"] != user_id:
            return None
        session["state"][TITLE_KEY] = title
        session["state"][TITLE_INITIALIZED_KEY] = True
        return session

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        self.users.append(user_id)
        # Deliberately omit remote filtering to prove the route's own boundary.
        return list(self.sessions.values())

    async def get_session(
        self, user_id: str, session_id: str, *, include_events: bool = True
    ) -> dict[str, Any] | None:
        self.users.append(user_id)
        return self.sessions.get(session_id)

    async def stream_query(self, user_id: str, session_id: str, message: str):
        self.queries.append((user_id, session_id, message))
        if self.block is not None:
            await self.block.wait()
        if self.failure:
            raise self.failure
        for current in self.events:
            yield current


def settings(**kwargs: Any) -> GatewaySettings:
    return GatewaySettings(
        cookie_secret=SECRET, allowed_origins=frozenset({ORIGIN}), **kwargs
    )


def sse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    result = []
    for block in text.split("\n\n"):
        if not block.startswith("event: "):
            continue
        kind, data = block.split("\n", 1)
        result.append(
            (kind.removeprefix("event: "), json.loads(data.removeprefix("data: ")))
        )
    return result


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def client(backend: FakeBackend):
    with TestClient(create_app(settings=settings(), backend=backend)) as result:
        yield result


def test_chat_issues_signed_http_only_cookie_and_managed_title(client, backend):
    response = client.post(
        "/api/chat", headers=HEADERS, json={"message": "  Return my shoes  "}
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/event-stream")
    assert backend.users[0].startswith("visitor-")
    assert backend.queries == [(backend.users[0], "session-1", "Return my shoes")]
    assert backend.sessions["session-1"]["state"][TITLE_KEY] == "Return my shoes"
    assert sse_events(response.text) == [
        ("session", {"conversationId": "session-1", "title": "Return my shoes"}),
        ("text", {"text": "Your support reply.", "mode": "replace"}),
        ("done", {}),
    ]
    listing = client.get("/api/conversations").json()["conversations"]
    assert listing[0]["title"] == "Return my shoes"
    assert listing[0]["updatedAt"].endswith("+00:00")


def test_visitor_cannot_choose_a_cloud_identity(client, backend):
    response = client.post(
        "/api/chat",
        headers=HEADERS,
        json={"message": "hello", "user_id": "another-customer"},
    )
    assert response.status_code == 400
    assert not backend.users and not backend.queries
    assert "another-customer" not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://other.example"},
        {
            "Origin": ORIGIN,
            "Sec-Fetch-Site": "cross-site",
        },
    ],
)
def test_post_rejects_missing_or_foreign_origin(client, backend, headers):
    response = client.post("/api/chat", headers=headers, json={"message": "hello"})
    assert response.status_code == 403
    assert not backend.users and not backend.queries


def test_cross_origin_history_is_rejected(client, backend):
    response = client.get(
        "/api/conversations", headers={"Origin": "https://other.example"}
    )
    assert response.status_code == 403
    assert not backend.users


def test_body_limit_applies_before_cloud_calls(client, backend):
    body = b" " * 40_001
    response = client.post(
        "/api/chat",
        headers={**HEADERS, "Content-Type": "application/json"},
        content=body,
    )
    assert response.status_code == 413
    assert not backend.users and not backend.queries


@pytest.mark.parametrize(
    "message,ensure_ascii",
    [
        ("界" * 6_000, False),
        ("界" * 6_000, True),
        ('"' * 6_000, True),
    ],
)
def test_wire_body_accepts_valid_multibyte_and_escaped_character_limit(
    client, backend, message, ensure_ascii
):
    body = json.dumps({"message": message}, ensure_ascii=ensure_ascii).encode("utf-8")
    response = client.post(
        "/api/chat",
        headers={**HEADERS, "Content-Type": "application/json"},
        content=body,
    )
    assert response.status_code == 200
    assert backend.queries[0][2] == message
    assert sse_events(response.text)[-1] == ("done", {})


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "  "},
        {"message": 42},
        {"message": "x" * 6_001},
        {"message": "hello", "conversationId": "../foreign"},
    ],
)
def test_input_validation_never_reflects_or_dispatches_bad_content(
    client, backend, payload
):
    response = client.post("/api/chat", headers=HEADERS, json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["retryable"] is False
    assert not backend.queries


def test_lists_and_history_are_scoped_to_cookie_owner(client, backend):
    client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    backend.sessions["foreign"] = {
        "id": "foreign",
        "user_id": "other-owner",
        "state": {},
        "last_update_time": 1_759_300_001,
        "events": [event("customer_service", "private transcript")],
    }
    assert [
        item["id"] for item in client.get("/api/conversations").json()["conversations"]
    ] == ["session-1"]
    response = client.get("/api/conversations/foreign")
    assert response.status_code == 404 and "private transcript" not in response.text
    response = client.post(
        "/api/chat",
        headers=HEADERS,
        json={"message": "hello", "conversationId": "foreign"},
    )
    assert response.status_code == 404
    assert len(backend.queries) == 1


def test_tampered_cookie_cannot_reopen_another_visitors_session(client, backend):
    client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    previous_user = backend.users[0]
    cookie = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, cookie[:-1] + ("a" if cookie[-1] != "a" else "b"))
    assert client.get("/api/conversations/session-1").status_code == 404
    assert backend.users[-1] != previous_user


def test_stream_hides_reasoning_tools_and_specialists_but_preserves_evidence(
    client, backend
):
    backend.events = [
        event(
            "customer_service",
            content={
                "parts": [
                    {
                        "function_call": {
                            "name": "preload_memory",
                            "args": {"request": "private routing"},
                        },
                    }
                ]
            },
        ),
        event("returns_exchanges", "DETERMINATION: internal specialist output"),
        event(
            "customer_service",
            content={"parts": [{"text": "private reasoning", "thought": True}]},
        ),
        event(
            "customer_service",
            content={
                "parts": [
                    {
                        "function_response": {
                            "name": "returns_exchanges",
                            "response": {"result": "private tool data"},
                        },
                    }
                ]
            },
            actions={
                "state_delta": {
                    "policy:citations": [
                        {
                            "document": "tarnfield_returns_policy.pdf",
                            "excerpt": "Unworn shoes are eligible within 14 days.",
                            "passage_id": "passage-1",
                        }
                    ]
                }
            },
        ),
        event("customer_service", "Your shoes ", partial=True),
        event("customer_service", "are eligible.", partial=True),
        event("customer_service", "Your shoes are eligible."),
    ]
    response = client.post("/api/chat", headers=HEADERS, json={"message": "return"})
    events = sse_events(response.text)
    assert ("status", {"label": "Recalling useful details"}) in events
    assert [data for kind, data in events if kind == "text"] == [
        {"text": "Your shoes ", "mode": "append"},
        {"text": "are eligible.", "mode": "append"},
        {"text": "Your shoes are eligible.", "mode": "replace"},
    ]
    sources = next(data["sources"] for kind, data in events if kind == "sources")
    assert sources == [
        {
            "title": "Returns & exchanges policy",
            "document": "tarnfield_returns_policy.pdf",
            "excerpt": "Unworn shoes are eligible within 14 days.",
            "passageId": "passage-1",
        }
    ]
    for hidden in (
        "private routing",
        "DETERMINATION",
        "private reasoning",
        "private tool data",
        "private@example.test",
    ):
        assert hidden not in response.text


def test_model_prose_cannot_fabricate_source_metadata(client, backend):
    backend.events = [
        event(
            "customer_service", "Source: tarnfield_billing_policy.pdf — invented rule"
        )
    ]
    response = client.post("/api/chat", headers=HEADERS, json={"message": "refund"})
    assert not [kind for kind, _ in sse_events(response.text) if kind == "sources"]


def test_restored_history_omits_internal_events_and_restores_policy_sources(
    client, backend
):
    client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    backend.sessions["session-1"]["events"] = [
        event("user", "Can I return these?"),
        event("returns_exchanges", "private specialist notes"),
        event(
            "customer_service",
            content={
                "parts": [
                    {
                        "function_response": {
                            "name": "get_returns_context",
                            "response": {
                                "policy_evidence": "[tarnfield_returns_policy.pdf] The return window is 14 days.",
                            },
                        },
                    }
                ]
            },
        ),
        event("customer_service", "Yes", partial=True),
        event(
            "customer_service",
            "Yes, within 14 days.",
            actions={
                "state_delta": {
                    "policy:citations": [
                        {
                            "document": "tarnfield_returns_policy.pdf",
                            "excerpt": "The return window is 14 days.",
                            "passage_id": "passage-2",
                        }
                    ],
                }
            },
        ),
    ]
    result = client.get("/api/conversations/session-1").json()
    assert result["conversationId"] == "session-1"
    assert [
        (message["role"], message["content"]) for message in result["messages"]
    ] == [
        ("user", "Can I return these?"),
        ("assistant", "Yes, within 14 days."),
    ]
    assert (
        result["messages"][1]["sources"][0]["document"]
        == "tarnfield_returns_policy.pdf"
    )


def test_cloud_failure_is_sanitized_and_releases_turn_guard(client, backend):
    backend.failure = RuntimeError("Bearer secret-value cloud-project credential-path")
    response = client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    events = sse_events(response.text)
    assert [kind for kind, _ in events] == ["session", "error"]
    assert events[-1][1]["retryable"] is False
    assert "secret-value" not in response.text
    backend.failure = None
    assert (
        client.post(
            "/api/chat",
            headers=HEADERS,
            json={"message": "hello", "conversationId": "session-1"},
        ).status_code
        == 200
    )


def test_timeout_does_not_invite_a_duplicate_action(backend):
    backend.block = asyncio.Event()
    with TestClient(
        create_app(settings=settings(turn_timeout=0.01), backend=backend)
    ) as client:
        response = client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
        events = sse_events(response.text)
        assert events[-1][0] == "error"
        assert "recover any saved answer" in events[-1][1]["message"]
        assert events[-1][1]["retryable"] is False
        assert not client.app.state.active_visitors


@pytest.mark.asyncio
async def test_disconnect_keeps_guard_until_the_actual_workflow_finishes(backend):
    backend.block = asyncio.Event()
    app = create_app(settings=settings(), backend=backend)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/api/chat")
    visitor = "a" * 32
    cookie = _signed_visitor(settings(), visitor, int(time.time()))

    def request() -> Request:
        body = json.dumps({"message": "hello"}).encode()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/chat",
                "headers": [
                    (b"origin", ORIGIN.encode()),
                    (b"content-type", b"application/json"),
                    (b"cookie", f"{COOKIE_NAME}={cookie}".encode()),
                ],
            },
            receive=receive,
        )

    response = await endpoint(request())
    assert "event: session" in await response.body_iterator.__anext__()
    await response.body_iterator.aclose()
    with pytest.raises(GatewayError) as busy:
        await endpoint(request())
    assert busy.value.status == 409
    assert backend.queries == [(f"visitor-{visitor}", "session-1", "hello")]
    backend.block.set()
    await asyncio.gather(*app.state.turn_tasks)
    assert not app.state.active_visitors


def test_production_configuration_requires_secret_and_exact_https_origins(monkeypatch):
    monkeypatch.setenv("GATEWAY_ENV", "production")
    monkeypatch.delenv("GATEWAY_COOKIE_SECRET", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOWED_ORIGINS", raising=False)
    with pytest.raises(RuntimeError, match="Production needs"):
        GatewaySettings.from_env()
    monkeypatch.setenv("GATEWAY_COOKIE_SECRET", SECRET)
    monkeypatch.setenv("GATEWAY_ALLOWED_ORIGINS", "http://localhost:3000")
    with pytest.raises(RuntimeError, match="exact origins"):
        GatewaySettings.from_env()
    monkeypatch.setenv("GATEWAY_ALLOWED_ORIGINS", "https://support.example.test")
    configured = GatewaySettings.from_env()
    with TestClient(create_app(settings=configured, backend=FakeBackend())) as client:
        response = client.get("/api/conversations")
        assert "Secure" in response.headers["set-cookie"]


def test_health_only_reports_gateway_process(client, backend):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert not backend.users and not backend.queries


def test_bootstrap_sets_identity_before_cloud_work_and_reuses_cookie(client, backend):
    response = client.get("/api/bootstrap")
    assert response.json() == {"ready": True}
    token = client.cookies.get(COOKIE_NAME)
    assert token and "HttpOnly" in response.headers["set-cookie"]
    assert not backend.users and not backend.queries
    assert "set-cookie" not in client.get("/api/bootstrap").headers
    client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    assert backend.users[0] == f"visitor-{token.split('.')[0]}"


def test_partial_only_upstream_completion_is_reported_as_incomplete(client, backend):
    backend.events = [event("customer_service", "Incomplete reply", partial=True)]
    response = client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    events = sse_events(response.text)
    assert events[-1][0] == "error"
    assert not [kind for kind, _ in events if kind == "done"]


def test_history_recovers_filed_reference_when_the_final_reply_failed(client, backend):
    client.post("/api/chat", headers=HEADERS, json={"message": "exchange"})
    backend.sessions["session-1"]["events"] = [
        event("user", "Please file the exchange.")
    ]
    backend.sessions["session-1"]["state"]["user:open_request_refs"] = [
        {
            "reference": "REQ-A1B2C3D4E5",
            "kind": "exchange",
            "order_id": "TF-88455",
            "raw": "private",
        },
        {"reference": "invented reference", "kind": "return", "order_id": "TF-88455"},
        {"reference": "REQ-A1B2C3D4E6", "kind": "unknown action"},
    ]
    result = client.get("/api/conversations/session-1").json()
    assert "supportRequests" not in result
    assert result["messages"][0]["role"] == "user"
    assert "private" not in json.dumps(result)


def test_new_conversation_becomes_persisted_first_message_title(client, backend):
    client.get("/api/bootstrap")
    created = client.post("/api/conversations", headers=HEADERS, json={})
    assert created.status_code == 201
    assert created.json() == {
        "conversationId": "session-1",
        "title": "New conversation",
    }
    assert backend.sessions["session-1"]["state"][TITLE_INITIALIZED_KEY] is False
    response = client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "Can I return\n these trainers?",
            "conversationId": "session-1",
        },
    )
    assert response.status_code == 200
    assert sse_events(response.text)[0] == (
        "session",
        {
            "conversationId": "session-1",
            "title": "Can I return these trainers?",
        },
    )
    assert (
        client.get("/api/conversations").json()["conversations"][0]["title"]
        == "Can I return these trainers?"
    )
    assert (
        client.get("/api/conversations/session-1").json()["title"]
        == "Can I return these trainers?"
    )
    client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "Another question",
            "conversationId": "session-1",
        },
    )
    assert len(backend.title_updates) == 1
    assert (
        backend.sessions["session-1"]["state"][TITLE_KEY]
        == "Can I return these trainers?"
    )


def test_literal_placeholder_as_first_message_is_not_renamed_on_followup(
    client, backend
):
    client.get("/api/bootstrap")
    client.post("/api/conversations", headers=HEADERS, json={})
    client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "New conversation",
            "conversationId": "session-1",
        },
    )
    client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "Follow-up",
            "conversationId": "session-1",
        },
    )
    assert len(backend.title_updates) == 1
    assert backend.sessions["session-1"]["state"][TITLE_KEY] == "New conversation"


def test_creation_error_is_explicit_safe_and_releases_guard(client, backend):
    client.get("/api/bootstrap")
    backend.creation_failure = RuntimeError("private cloud credential path")
    response = client.post("/api/conversations", headers=HEADERS, json={})
    assert response.status_code == 503
    assert response.json()["error"] == {
        "message": "We couldn't create your conversation. Please try again.",
        "retryable": True,
    }
    assert "credential" not in response.text
    assert not client.app.state.active_visitors and not backend.queries
    backend.creation_failure = None
    assert (
        client.post("/api/conversations", headers=HEADERS, json={}).status_code == 201
    )


def test_title_save_failure_never_dispatches_the_message(client, backend):
    client.get("/api/bootstrap")
    client.post("/api/conversations", headers=HEADERS, json={})
    backend.title_failure = RuntimeError("private SDK details")
    response = client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "Exchange my trainers",
            "conversationId": "session-1",
        },
    )
    assert response.status_code == 503
    assert "Your message wasn't sent" in response.json()["error"]["message"]
    assert response.json()["error"]["retryable"] is True
    assert not backend.queries and not client.app.state.active_visitors
    backend.title_failure = None
    assert (
        client.post(
            "/api/chat",
            headers=HEADERS,
            json={
                "message": "Exchange my trainers",
                "conversationId": "session-1",
            },
        ).status_code
        == 200
    )


@pytest.mark.asyncio
async def test_title_sdk_write_is_state_only_and_preserves_other_session_data():
    from google.adk.sessions import Session

    session = Session(
        id="session-1",
        user_id="visitor-owner",
        app_name="runtime",
        state={
            TITLE_KEY: "New conversation",
            TITLE_INITIALIZED_KEY: False,
            "user:contact_method": "email",
        },
    )
    saved = []

    class FakeSessions:
        async def get_session(self, **kwargs):
            assert kwargs["user_id"] == "visitor-owner"
            assert kwargs["config"].num_recent_events == 0
            return session

        async def append_event(self, *, session, event):
            saved.append(event)
            session.state.update(event.actions.state_delta)

    backend = ManagedSessionBackend()
    backend._session_service = FakeSessions()
    backend._runtime_name = "runtime"
    result = await backend.set_conversation_title(
        "visitor-owner", "session-1", "Returns question"
    )
    assert result["state"][TITLE_KEY] == "Returns question"
    assert result["state"]["user:contact_method"] == "email"
    assert saved[0].author == "chat_gateway" and saved[0].content is None
    assert set(saved[0].actions.state_delta) == {TITLE_KEY, TITLE_INITIALIZED_KEY}
    await backend.set_conversation_title("visitor-owner", "session-1", "Follow-up")
    assert len(saved) == 1


def test_history_and_busy_errors_identify_only_callers_active_conversation(
    client, backend
):
    token = client.get("/api/bootstrap").headers["set-cookie"]
    assert token
    client.post("/api/conversations", headers=HEADERS, json={})
    client.post("/api/conversations", headers=HEADERS, json={})
    user_id = backend.users[0]
    client.app.state.active_visitors.add(user_id)
    client.app.state.active_conversations[user_id] = "session-1"
    current = client.get("/api/conversations/session-1").json()
    other = client.get("/api/conversations/session-2").json()
    assert current["active"] is True and current["inFlight"] is True
    assert other["active"] is True and other["inFlight"] is False
    blocked = client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "hello",
            "conversationId": "session-2",
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"] == {
        "message": "Please wait for your current support task to finish.",
        "retryable": False,
        "code": "conversation_busy",
        "active": True,
        "conversationId": "session-1",
    }
    assert (
        client.post("/api/conversations", headers=HEADERS, json={}).status_code == 409
    )
    assert not backend.queries


def test_completed_stream_exposes_only_saved_support_references(client, backend):
    client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    backend.sessions["session-1"]["state"]["user:open_request_refs"] = [
        {
            "reference": "REQ-A1B2C3D4E5",
            "kind": "return",
            "order_id": "TF-88213",
            "private_details": "do not expose",
        }
    ]
    response = client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "Check the request",
            "conversationId": "session-1",
        },
    )
    events = sse_events(response.text)
    assert not any(kind == "supportRequests" for kind, _ in events)
    assert "supportRequests" not in client.get("/api/conversations/session-1").json()
    assert events[-1] == ("done", {})
    assert "do not expose" not in response.text


def test_reference_refresh_failure_does_not_discard_a_completed_reply(client, backend):
    async def unavailable(*args, **kwargs):
        raise RuntimeError("private SDK response")

    backend.get_session = unavailable
    response = client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    events = sse_events(response.text)
    assert events[-1] == ("done", {})
    assert not [kind for kind, _ in events if kind == "error"]
    assert "private SDK response" not in response.text


def test_metadata_with_null_parts_does_not_break_the_customer_stream(client, backend):
    backend.events = [
        event("customer_service", content={"parts": None}),
        event("customer_service", "A complete reply."),
    ]
    response = client.post("/api/chat", headers=HEADERS, json={"message": "hello"})
    assert sse_events(response.text)[-1] == ("done", {})


def test_deeply_nested_invalid_json_is_a_safe_bad_request(client, backend):
    body = '{"message":' + "[" * 1_500 + "0" + "]" * 1_500 + "}"
    response = client.post(
        "/api/chat",
        headers={**HEADERS, "Content-Type": "application/json"},
        content=body,
    )
    assert response.status_code == 400
    assert not backend.queries


@pytest.mark.asyncio
async def test_concurrent_session_creation_is_guarded_for_one_visitor(backend):
    backend.creation_block = asyncio.Event()
    app = create_app(settings=settings(), backend=backend)
    endpoint = next(
        route.endpoint
        for route in app.routes
        if route.path == "/api/conversations" and "POST" in route.methods
    )
    visitor = "b" * 32
    cookie = _signed_visitor(settings(), visitor, int(time.time()))

    def request():
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/conversations",
                "headers": [
                    (b"origin", ORIGIN.encode()),
                    (b"content-type", b"application/json"),
                    (b"cookie", f"{COOKIE_NAME}={cookie}".encode()),
                ],
            },
            receive=receive,
        )

    first = asyncio.create_task(endpoint(request()))
    await asyncio.sleep(0)
    with pytest.raises(GatewayError) as busy:
        await endpoint(request())
    assert busy.value.status == 409
    assert busy.value.metadata["code"] == "conversation_busy"
    backend.creation_block.set()
    assert (await first).status_code == 201
    assert len(backend.sessions) == 1
    assert not app.state.active_visitors


def test_list_recovers_callers_active_turn_before_and_after_session_id(client, backend):
    client.get("/api/bootstrap")
    cookie = client.cookies.get(COOKIE_NAME)
    user_id = f"visitor-{cookie.split('.')[0]}"
    client.app.state.active_visitors.add(user_id)
    creating = client.get("/api/conversations").json()
    assert creating["active"] is True and creating["activeConversationId"] is None
    backend.sessions["session-1"] = {
        "id": "session-1",
        "user_id": user_id,
        "state": {TITLE_KEY: "Recovery question", TITLE_INITIALIZED_KEY: True},
        "last_update_time": 1_759_300_000,
        "events": [],
    }
    client.app.state.active_conversations[user_id] = "session-1"
    dispatched = client.get("/api/conversations").json()
    assert (
        dispatched["active"] is True
        and dispatched["activeConversationId"] == "session-1"
    )
    client.cookies.clear()
    other_visitor = client.get("/api/conversations").json()
    assert (
        other_visitor["active"] is False
        and other_visitor["activeConversationId"] is None
    )
    assert other_visitor["conversations"] == []
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, cookie)
    client.app.state.active_visitors.discard(user_id)
    client.app.state.active_conversations.pop(user_id)
    response = client.post(
        "/api/chat",
        headers=HEADERS,
        json={
            "message": "Continue",
            "conversationId": "session-1",
        },
    )
    assert sse_events(response.text)[-1] == ("done", {})
    completed = client.get("/api/conversations").json()
    assert completed["active"] is False and completed["activeConversationId"] is None
