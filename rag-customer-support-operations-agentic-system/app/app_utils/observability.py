"""Automatic ADK tracing to Arize AX, alongside existing Google Cloud telemetry.

Call setup after ADK has configured its Cloud tracer provider and before serving
agent requests. Instrumentation creates spans; this module only exports them.
"""

from __future__ import annotations

import atexit
import copy
import logging
import os
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from openinference.instrumentation import TraceConfig
from openinference.instrumentation.config import REDACTED_VALUE
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Link, Status

logger = logging.getLogger(__name__)

DEFAULT_PROJECT = "tarnfield-customer-bot"
DEFAULT_ENDPOINT = "https://otlp.arize.com/v1/traces"
EXPORT_TIMEOUT_SECONDS = 5
FLUSH_TIMEOUT_MILLIS = 5000
PROJECT_ATTRIBUTE = "openinference.project.name"
ADK_INSTRUMENTATION_SCOPE = "openinference.instrumentation.google_adk"

# This list defines the metadata-only export boundary. Unknown attributes are
# omitted rather than assuming future instrumentors' payload fields are safe.
_SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "openinference.span.kind",
        "agent.name",
        "session.id",
        "user.id",
        "tool.name",
        "tool.id",
        "llm.model_name",
        "llm.provider",
        "llm.system",
        "gen_ai.operation.name",
        "gen_ai.agent.name",
        "gen_ai.tool.name",
        "gen_ai.tool.type",
        "gen_ai.tool.call.id",
        "gen_ai.system",
        "gen_ai.provider.name",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "gen_ai.response.finish_reasons",
        "gen_ai.conversation.id",
        "gcp.vertex.agent.invocation_id",
        "gcp.vertex.agent.session_id",
        "gcp.vertex.agent.event_id",
        "error.type",
        "exception.type",
    }
)
_SAFE_ATTRIBUTE_PREFIXES = ("llm.token_count.", "gen_ai.usage.")


def _redacted_span_copy(span: ReadableSpan) -> ReadableSpan:
    """Keep trace structure and usage, omit payloads and error-message content."""
    sanitized = copy.copy(span)
    attrs = {}
    for key, value in (span.attributes or {}).items():
        if key in ("input.value", "output.value"):
            attrs[key] = REDACTED_VALUE
        elif key in _SAFE_ATTRIBUTE_KEYS:
            attrs[key] = value
        elif key.startswith(_SAFE_ATTRIBUTE_PREFIXES) and isinstance(
            value, (int, float)
        ):
            attrs[key] = value
    sanitized._attributes = attrs
    sanitized._status = Status(span.status.status_code)
    sanitized._events = [
        Event(
            name="exception",
            attributes={
                key: value
                for key, value in (event.attributes or {}).items()
                if key in ("exception.type", "exception.escaped")
            },
            timestamp=event.timestamp,
        )
        for event in span.events
        if event.name == "exception"
    ]
    sanitized._links = [Link(context=link.context) for link in span.links]
    return sanitized


@dataclass(frozen=True)
class _Settings:
    api_key: str = field(repr=False)
    space_id: str
    project_name: str
    endpoint: str
    capture_content: bool = False


@dataclass
class _State:
    provider: TracerProvider
    processor: BatchSpanProcessor
    instrumentor: object
    exporter: _ProjectExporter
    closed: bool = False
    flush_thread: threading.Thread | None = None
    flush_done: threading.Event = field(default_factory=threading.Event)
    flush_result: bool = False


_lock = threading.RLock()
_state: _State | None = None


def _settings() -> _Settings | None:
    enabled = os.getenv("ARIZE_ENABLED", "false").strip().lower()
    if enabled in ("", "false", "0", "no", "off"):
        return None
    if enabled not in ("true", "1", "yes", "on"):
        raise ValueError("ARIZE_ENABLED must be true or false.")

    api_key = os.getenv("ARIZE_API_KEY", "").strip()
    space_id = os.getenv("ARIZE_SPACE_ID", "").strip()
    missing = [
        name
        for name, value in (("ARIZE_API_KEY", api_key), ("ARIZE_SPACE_ID", space_id))
        if not value
    ]
    if missing:
        raise ValueError(
            "Arize tracing is enabled; configure " + ", ".join(missing) + "."
        )

    project = os.getenv("ARIZE_PROJECT_NAME", DEFAULT_PROJECT).strip()
    if not project:
        raise ValueError("ARIZE_PROJECT_NAME must not be empty.")
    endpoint = os.getenv("ARIZE_COLLECTOR_ENDPOINT", DEFAULT_ENDPOINT).strip()
    try:
        parsed = urlsplit(endpoint)
        valid_endpoint = (
            parsed.scheme == "https"
            and parsed.hostname in ("otlp.arize.com", "otlp.eu-west-1a.arize.com")
            and parsed.path == "/v1/traces"
            and parsed.port in (None, 443)
            and not (
                parsed.username or parsed.password or parsed.query or parsed.fragment
            )
        )
    except ValueError:
        valid_endpoint = False
    if not valid_endpoint:
        raise ValueError(
            "ARIZE_COLLECTOR_ENDPOINT must be an Arize AX HTTPS /v1/traces endpoint."
        )
    if any("\r" in value or "\n" in value for value in (api_key, space_id)):
        raise ValueError("Arize credentials must not contain line breaks.")
    capture = os.getenv("ARIZE_CAPTURE_CONTENT", "false").strip().lower()
    if capture not in ("", "true", "1", "yes", "on", "false", "0", "no", "off"):
        raise ValueError("ARIZE_CAPTURE_CONTENT must be true or false.")
    return _Settings(
        api_key, space_id, project, endpoint, capture in ("true", "1", "yes", "on")
    )


class _ContentSafeTraceConfig(TraceConfig):
    """Also mask tool arguments and ADK legacy attributes omitted by TraceConfig."""

    def mask(self, key, value, *, externalize=True):
        if (self.hide_inputs or self.hide_outputs) and key in {
            "tool.parameters",
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
            "gen_ai.input.messages",
            "gen_ai.output.messages",
            "gcp.vertex.agent.llm_request",
            "gcp.vertex.agent.llm_response",
            "gcp.vertex.agent.tool_call_args",
            "gcp.vertex.agent.tool_response",
            "gcp.vertex.agent.data",
        }:
            return None
        return super().mask(key, value, externalize=externalize)


class _ProjectExporter(SpanExporter):
    """Attach Arize routing to exported copies; leave Cloud spans untouched."""

    def __init__(
        self, exporter: SpanExporter, project_name: str, capture_content: bool = False
    ):
        self._exporter = exporter
        self._resource = Resource({PROJECT_ATTRIBUTE: project_name})
        self._capture_content = capture_content
        self._lock = threading.Lock()
        self._diagnostics = {
            "attempted_batches": 0,
            "successful_batches": 0,
            "failed_batches": 0,
            "attempted_spans": 0,
            "successful_spans": 0,
        }

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        arize_spans = []
        for span in spans:
            # Native GenAI spans duplicate ADK call_llm tokens/cost and can
            # bypass OI TraceConfig. Export exactly the instrumentor we own;
            # Cloud processors still receive every original span unchanged.
            scope = span.instrumentation_scope
            if (
                scope is None
                or scope.name != ADK_INSTRUMENTATION_SCOPE
                or not (span.attributes or {}).get("openinference.span.kind")
            ):
                continue
            exported_span = (
                copy.copy(span) if self._capture_content else _redacted_span_copy(span)
            )
            exported_span._resource = span.resource.merge(self._resource)
            arize_spans.append(exported_span)
        if not arize_spans:
            return SpanExportResult.SUCCESS
        with self._lock:
            self._diagnostics["attempted_batches"] += 1
            self._diagnostics["attempted_spans"] += len(arize_spans)
        try:
            result = self._exporter.export(arize_spans)
        except Exception:
            # Transport exceptions may contain headers. Never log their text
            # or traceback, and never let an export failure affect a chat turn.
            result = SpanExportResult.FAILURE
        with self._lock:
            if result is SpanExportResult.SUCCESS:
                self._diagnostics["successful_batches"] += 1
                self._diagnostics["successful_spans"] += len(arize_spans)
            else:
                self._diagnostics["failed_batches"] += 1
        if result is not SpanExportResult.SUCCESS:
            logger.warning(
                "Arize trace export failed; check collector connectivity and credentials."
            )
        return result

    def diagnostics(self) -> dict[str, int]:
        with self._lock:
            return dict(self._diagnostics)

    def shutdown(self) -> None:
        self._exporter.shutdown()


def _create_exporter(settings: _Settings) -> SpanExporter:
    from arize.otel import HTTPSpanExporter

    return HTTPSpanExporter(
        space_id=settings.space_id,
        api_key=settings.api_key,
        endpoint=settings.endpoint,
        timeout=EXPORT_TIMEOUT_SECONDS,
    )


def setup_arize_observability() -> bool:
    """Enable automatic streamed agent, LLM and tool spans once per process.

    Returns false when explicitly disabled. Enabled but invalid configuration
    fails startup visibly without exposing credentials. An existing SDK provider
    and all of its processors are retained.
    """
    global _state
    with _lock:
        settings = _settings()
        if settings is None:
            return False
        if _state is not None:
            if _state.closed:
                raise RuntimeError(
                    "Arize tracing was shut down; restart the process to enable it again."
                )
            if trace.get_tracer_provider() is not _state.provider:
                raise RuntimeError(
                    "The tracer provider changed after Arize setup; restart the process."
                )
            return True

        from openinference.instrumentation.google_adk import GoogleADKInstrumentor

        instrumentor = GoogleADKInstrumentor()
        if instrumentor.is_instrumented_by_opentelemetry:
            raise RuntimeError(
                "Google ADK tracing is already instrumented outside Arize setup."
            )

        provider = trace.get_tracer_provider()
        if isinstance(provider, trace.ProxyTracerProvider):
            provider = TracerProvider(
                resource=Resource.create({"service.name": "customer-chatbot-rag"})
            )
            trace.set_tracer_provider(provider)
            if trace.get_tracer_provider() is not provider:
                provider.shutdown()
                raise RuntimeError("Could not initialize the Arize tracer provider.")
        if not isinstance(provider, TracerProvider):
            raise RuntimeError(
                "Arize tracing requires an OpenTelemetry SDK tracer provider."
            )

        exporter = None
        processor = None
        try:
            exporter = _ProjectExporter(
                _create_exporter(settings),
                settings.project_name,
                capture_content=settings.capture_content,
            )
            processor = BatchSpanProcessor(
                exporter,
                max_queue_size=512,
                max_export_batch_size=128,
                schedule_delay_millis=1000,
                export_timeout_millis=FLUSH_TIMEOUT_MILLIS,
            )
            instrumentor.instrument(
                tracer_provider=provider,
                raise_exception_on_conflict=True,
                config=_ContentSafeTraceConfig(
                    hide_inputs=not settings.capture_content,
                    hide_outputs=not settings.capture_content,
                    hide_llm_invocation_parameters=not settings.capture_content,
                ),
            )
            if not instrumentor.is_instrumented_by_opentelemetry:
                raise RuntimeError("Automatic ADK instrumentation did not initialize.")
            provider.add_span_processor(processor)
        except Exception:
            if instrumentor.is_instrumented_by_opentelemetry:
                try:
                    instrumentor.uninstrument()
                except Exception:
                    logger.warning("ADK instrumentation cleanup failed during startup.")
            try:
                if processor is not None:
                    processor.shutdown()
                elif exporter is not None:
                    exporter.shutdown()
            except Exception:
                logger.warning("Arize exporter cleanup failed during startup.")
            raise RuntimeError(
                "Arize automatic tracing could not initialize; check configuration and dependency versions."
            ) from None

        _state = _State(provider, processor, instrumentor, exporter)
        atexit.register(shutdown_arize_observability)
        logger.info(
            "Arize AX automatic ADK tracing enabled for project %s.",
            settings.project_name,
        )
        return True


def _flush_state(state: _State, timeout_millis: int) -> bool:
    # OTel 1.42's BatchSpanProcessor ignores force_flush's timeout argument.
    # Bound the caller's wait ourselves and reuse an in-flight flush on retries.
    if state.flush_thread is None or state.flush_done.is_set():
        state.flush_done.clear()
        state.flush_result = False

        def flush():
            try:
                state.flush_result = bool(
                    state.processor.force_flush(timeout_millis=timeout_millis)
                )
            except Exception:
                logger.warning("Arize trace flush failed.")
            finally:
                state.flush_done.set()

        state.flush_thread = threading.Thread(
            target=flush, name="arize-flush", daemon=True
        )
        state.flush_thread.start()
    return state.flush_done.wait(timeout_millis / 1000) and state.flush_result


def flush_arize_observability(timeout_millis: int = FLUSH_TIMEOUT_MILLIS) -> bool:
    """Wait for queued exports; consult diagnostics to confirm ingestion success."""
    if timeout_millis <= 0:
        raise ValueError("The Arize flush timeout must be positive.")
    with _lock:
        if _state is None or _state.closed:
            return False
        try:
            return _flush_state(_state, timeout_millis)
        except Exception:
            logger.warning("Arize trace flush failed.")
            return False


def get_arize_export_diagnostics() -> dict[str, int]:
    """Return process-local delivery counts without credentials or trace content."""
    with _lock:
        if _state is None:
            return {
                "attempted_batches": 0,
                "successful_batches": 0,
                "failed_batches": 0,
                "attempted_spans": 0,
                "successful_spans": 0,
            }
        return _state.exporter.diagnostics()


def shutdown_arize_observability() -> None:
    """Flush and close only Arize export; the Cloud provider remains active.

    This is a synchronous cleanup hook; async server lifespans should call it via
    asyncio.to_thread after closing their runners. The OTLP exporter has a five
    second network deadline and its batch queue is bounded.
    """
    with _lock:
        if _state is None or _state.closed:
            return
        _state.closed = True
        try:
            if not _flush_state(_state, FLUSH_TIMEOUT_MILLIS):
                logger.warning(
                    "Arize trace flush did not finish before the shutdown deadline."
                )
                # Stop retries and new exports before closing the batch worker.
                _state.exporter.shutdown()
        except Exception:
            logger.warning("Arize trace flush failed during shutdown.")
        finally:
            try:
                _state.processor.shutdown()
            except Exception:
                logger.warning("Arize exporter cleanup failed during shutdown.")
