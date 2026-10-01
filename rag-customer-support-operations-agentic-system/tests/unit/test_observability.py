"""Automatic tracing bootstrap and delivery tests with no network calls."""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest
from openinference.instrumentation import OITracer
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceFlags

from app.app_utils import observability

_create_http_exporter = observability._create_exporter


class FakeInstrumentor:
    def __init__(self):
        self.is_instrumented_by_opentelemetry = False
        self.calls = []

    def instrument(self, **kwargs):
        self.calls.append(kwargs)
        self.is_instrumented_by_opentelemetry = True

    def uninstrument(self):
        self.is_instrumented_by_opentelemetry = False


@pytest.fixture
def tracing(monkeypatch):
    monkeypatch.setenv("ARIZE_ENABLED", "true")
    monkeypatch.setenv("ARIZE_API_KEY", "test-only-secret")
    monkeypatch.setenv("ARIZE_SPACE_ID", "test-space")
    for name in (
        "ARIZE_PROJECT_NAME",
        "ARIZE_COLLECTOR_ENDPOINT",
        "ARIZE_CAPTURE_CONTENT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(observability, "_state", None)
    provider = TracerProvider(resource=Resource({"service.name": "cloud-service"}))
    cloud = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(cloud))
    exported = InMemorySpanExporter()
    instrumentor = FakeInstrumentor()
    exporter_settings = []

    def create_exporter(settings):
        exporter_settings.append(settings)
        return exported

    import openinference.instrumentation.google_adk as google_adk

    monkeypatch.setattr(google_adk, "GoogleADKInstrumentor", lambda: instrumentor)
    monkeypatch.setattr(observability.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(observability, "_create_exporter", create_exporter)
    monkeypatch.setattr(observability.atexit, "register", lambda callback: None)

    def reject_network(*args, **kwargs):
        raise AssertionError("Observability tests must not perform network requests.")

    import requests

    monkeypatch.setattr(requests.Session, "request", reject_network)
    yield SimpleNamespace(
        provider=provider,
        cloud=cloud,
        exported=exported,
        instrumentor=instrumentor,
        settings=exporter_settings,
    )
    observability.shutdown_arize_observability()
    provider.shutdown()


def test_disabled_does_not_configure_exporter_or_instrumentor(tracing, monkeypatch):
    monkeypatch.setenv("ARIZE_ENABLED", "false")
    assert observability.setup_arize_observability() is False
    assert tracing.settings == []
    assert tracing.instrumentor.calls == []
    assert observability.flush_arize_observability() is False
    assert observability.get_arize_export_diagnostics()["attempted_spans"] == 0


@pytest.mark.parametrize("missing", ["ARIZE_API_KEY", "ARIZE_SPACE_ID"])
def test_enabled_missing_credentials_fails_visibly_without_secret(
    tracing, monkeypatch, missing
):
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match=missing) as error:
        observability.setup_arize_observability()
    assert "test-only-secret" not in str(error.value)
    assert not tracing.settings


@pytest.mark.parametrize(
    "name,value",
    [
        ("ARIZE_ENABLED", "maybe"),
        ("ARIZE_CAPTURE_CONTENT", "maybe"),
        ("ARIZE_PROJECT_NAME", " "),
        ("ARIZE_API_KEY", "test-only-secret\nheader"),
        ("ARIZE_COLLECTOR_ENDPOINT", "https://example.com/v1/traces"),
        ("ARIZE_COLLECTOR_ENDPOINT", "http://otlp.arize.com/v1/traces"),
        (
            "ARIZE_COLLECTOR_ENDPOINT",
            "https://test-only-secret@otlp.arize.com/v1/traces",
        ),
    ],
)
def test_invalid_configuration_is_safe_and_fails_before_instrumentation(
    tracing, monkeypatch, name, value
):
    monkeypatch.setenv(name, value)
    with pytest.raises(
        ValueError, match=name if name != "ARIZE_API_KEY" else "credentials"
    ) as error:
        observability.setup_arize_observability()
    assert "test-only-secret" not in str(error.value)
    assert not tracing.instrumentor.calls


def test_setup_is_idempotent_and_preserves_cloud_provider_and_processor(
    tracing, monkeypatch
):
    def forbid_replacement(*args, **kwargs):
        raise AssertionError("Existing Cloud provider must not be replaced.")

    monkeypatch.setattr(observability.trace, "set_tracer_provider", forbid_replacement)
    assert observability.setup_arize_observability() is True
    assert observability.setup_arize_observability() is True
    assert len(tracing.settings) == 1
    assert len(tracing.instrumentor.calls) == 1
    assert tracing.instrumentor.calls[0]["tracer_provider"] is tracing.provider
    assert tracing.settings[0].project_name == observability.DEFAULT_PROJECT
    assert tracing.settings[0].capture_content is False
    assert "test-only-secret" not in repr(tracing.settings[0])

    with tracing.provider.get_tracer(
        observability.ADK_INSTRUMENTATION_SCOPE
    ).start_as_current_span(
        "agent_run", attributes={"openinference.span.kind": "AGENT"}
    ):
        pass
    assert observability.flush_arize_observability()
    cloud_span = tracing.cloud.get_finished_spans()[0]
    arize_span = tracing.exported.get_finished_spans()[0]
    assert cloud_span.context == arize_span.context
    assert cloud_span.resource.attributes == {"service.name": "cloud-service"}
    assert arize_span.resource.attributes["service.name"] == "cloud-service"
    assert (
        arize_span.resource.attributes[observability.PROJECT_ATTRIBUTE]
        == observability.DEFAULT_PROJECT
    )
    assert observability.get_arize_export_diagnostics() == {
        "attempted_batches": 1,
        "successful_batches": 1,
        "failed_batches": 0,
        "attempted_spans": 1,
        "successful_spans": 1,
    }


def test_generic_server_spans_remain_in_cloud_without_polluting_arize(tracing):
    observability.setup_arize_observability()
    with tracing.provider.get_tracer(
        observability.ADK_INSTRUMENTATION_SCOPE
    ).start_as_current_span("http_request"):
        pass
    assert observability.flush_arize_observability()
    assert len(tracing.cloud.get_finished_spans()) == 1
    assert tracing.exported.get_finished_spans() == ()
    assert observability.get_arize_export_diagnostics()["attempted_batches"] == 0


def test_default_content_policy_masks_actual_openinference_spans_in_both_destinations(
    tracing,
):
    observability.setup_arize_observability()
    config = tracing.instrumentor.calls[0]["config"]
    tracer = OITracer(
        tracing.provider.get_tracer(observability.ADK_INSTRUMENTATION_SCOPE),
        config=config,
    )
    with tracer.start_as_current_span(
        "execute_tool",
        attributes={"openinference.span.kind": "TOOL", "tool.name": "lookup_order"},
    ) as span:
        for key in (
            "input.value",
            "output.value",
            "tool.parameters",
            "llm.invocation_parameters",
            "llm.input_messages.0.message.content",
            "llm.output_messages.0.message.content",
            "gen_ai.tool.call.arguments",
            "gen_ai.tool.call.result",
            "gcp.vertex.agent.llm_request",
            "gcp.vertex.agent.tool_response",
        ):
            span.set_attribute(key, "customer-private-content")
        span.set_attribute("session.id", "test-session")
        span.set_attribute("llm.model_name", "unchanged-model")
        span.set_attribute("llm.token_count.total", 17)
    assert observability.flush_arize_observability()
    for exporter in (tracing.cloud, tracing.exported):
        attrs = exporter.get_finished_spans()[0].attributes
        assert "customer-private-content" not in repr(attrs)
        assert attrs["tool.name"] == "lookup_order"
        assert attrs["session.id"] == "test-session"
        assert attrs["llm.token_count.total"] == 17
        assert attrs["llm.model_name"] == "unchanged-model"


def test_capture_content_requires_explicit_opt_in(tracing, monkeypatch):
    monkeypatch.setenv("ARIZE_CAPTURE_CONTENT", "true")
    observability.setup_arize_observability()
    tracer = OITracer(
        tracing.provider.get_tracer(observability.ADK_INSTRUMENTATION_SCOPE),
        config=tracing.instrumentor.calls[0]["config"],
    )
    with tracer.start_as_current_span(
        "tool", attributes={"openinference.span.kind": "TOOL"}
    ) as span:
        span.set_attribute("input.value", "explicitly-enabled-content")
        span.set_attribute("tool.parameters", "explicitly-enabled-content")
    assert observability.flush_arize_observability()
    for exporter in (tracing.cloud, tracing.exported):
        attrs = exporter.get_finished_spans()[0].attributes
        assert attrs["input.value"] == "explicitly-enabled-content"
        assert attrs["tool.parameters"] == "explicitly-enabled-content"


def test_startup_exporter_error_cannot_expose_credentials(tracing, monkeypatch, caplog):
    def broken(settings):
        raise RuntimeError("authorization=" + settings.api_key)

    monkeypatch.setattr(observability, "_create_exporter", broken)
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as error:
        observability.setup_arize_observability()
    assert "test-only-secret" not in str(error.value)
    assert "test-only-secret" not in caplog.text
    assert not tracing.instrumentor.calls
    assert observability._state is None


@pytest.mark.parametrize("raises", [True, False])
def test_real_export_failure_is_counted_without_logging_exception_contents(
    tracing, monkeypatch, caplog, raises
):
    def broken_export(spans):
        if raises:
            raise RuntimeError("authorization=test-only-secret")
        return SpanExportResult.FAILURE

    monkeypatch.setattr(tracing.exported, "export", broken_export)
    observability.setup_arize_observability()
    with tracing.provider.get_tracer(
        observability.ADK_INSTRUMENTATION_SCOPE
    ).start_as_current_span("agent", attributes={"openinference.span.kind": "AGENT"}):
        pass
    with caplog.at_level(logging.WARNING):
        # A drained batch queue is not proof that the collector accepted it.
        assert observability.flush_arize_observability()
    counts = observability.get_arize_export_diagnostics()
    assert counts["failed_batches"] == 1
    assert counts["successful_spans"] == 0
    assert counts["attempted_spans"] == 1
    assert "test-only-secret" not in caplog.text
    assert "trace export failed" in caplog.text


def test_shutdown_flushes_once_and_leaves_cloud_provider_running(tracing, monkeypatch):
    observability.setup_arize_observability()
    processor = observability._state.processor
    flush_calls = []
    shutdown_calls = []
    original_flush = processor.force_flush
    original_shutdown = processor.shutdown

    def flush(**kwargs):
        flush_calls.append(kwargs)
        return original_flush(**kwargs)

    def shutdown():
        shutdown_calls.append(True)
        original_shutdown()

    monkeypatch.setattr(processor, "force_flush", flush)
    monkeypatch.setattr(processor, "shutdown", shutdown)
    observability.shutdown_arize_observability()
    observability.shutdown_arize_observability()
    assert flush_calls == [{"timeout_millis": observability.FLUSH_TIMEOUT_MILLIS}]
    assert shutdown_calls == [True]
    with tracing.provider.get_tracer(
        observability.ADK_INSTRUMENTATION_SCOPE
    ).start_as_current_span("cloud_still_running"):
        pass
    assert tracing.cloud.get_finished_spans()[-1].name == "cloud_still_running"
    assert observability.flush_arize_observability() is False


def test_http_exporter_uses_ax_auth_and_bounded_network_deadline(tracing):
    # Call the real application factory without invoking its network export path.
    exporter = _create_http_exporter(observability._settings())
    assert exporter._endpoint == "https://otlp.arize.com/v1/traces"
    assert exporter._timeout == 5
    assert exporter._headers["authorization"] == "test-only-secret"
    assert exporter._headers["arize-space-id"] == "test-space"
    exporter.shutdown()


def test_shutdown_closes_exporter_even_when_flush_raises(tracing, monkeypatch, caplog):
    observability.setup_arize_observability()
    processor = observability._state.processor
    shutdown = processor.shutdown
    shutdown_calls = []

    def fail_flush(**kwargs):
        raise RuntimeError("authorization=test-only-secret")

    def close():
        shutdown_calls.append(True)
        shutdown()

    monkeypatch.setattr(processor, "force_flush", fail_flush)
    monkeypatch.setattr(processor, "shutdown", close)
    with caplog.at_level(logging.WARNING):
        observability.shutdown_arize_observability()
    assert shutdown_calls == [True]
    assert "flush failed" in caplog.text
    assert "test-only-secret" not in caplog.text


def test_flush_deadline_is_enforced_when_sdk_ignores_its_timeout(tracing, monkeypatch):
    observability.setup_arize_observability()
    unblock = threading.Event()
    calls = []

    def blocking_flush(**kwargs):
        calls.append(kwargs)
        unblock.wait(1)
        return True

    monkeypatch.setattr(observability._state.processor, "force_flush", blocking_flush)
    try:
        assert observability.flush_arize_observability(timeout_millis=10) is False
        assert observability.flush_arize_observability(timeout_millis=10) is False
        assert len(calls) == 1, "A retry must reuse the unfinished flush."
    finally:
        unblock.set()
        assert observability._state.flush_done.wait(1)


@pytest.mark.parametrize(
    "native_scope", ["gcp.vertex.agent", "opentelemetry.instrumentation.google_genai"]
)
def test_duplicate_native_model_spans_stay_in_cloud_and_do_not_inflate_arize_usage(
    tracing, native_scope
):
    observability.setup_arize_observability()
    with tracing.provider.get_tracer(
        observability.ADK_INSTRUMENTATION_SCOPE
    ).start_as_current_span(
        "call_llm",
        attributes={"openinference.span.kind": "LLM", "llm.token_count.total": 11},
    ):
        with tracing.provider.get_tracer(native_scope).start_as_current_span(
            "generate_content unchanged-model",
            attributes={
                "openinference.span.kind": "LLM",
                "gen_ai.operation.name": "generate_content",
                "code.function.name": "google.genai.AsyncModels.generate_content_stream",
                "llm.token_count.total": 11,
                "input.value": "native-private-content",
                "llm.input_messages.0.message.content": "native-private-content",
            },
        ):
            pass
    assert observability.flush_arize_observability()
    assert len(tracing.cloud.get_finished_spans()) == 2
    arize_spans = tracing.exported.get_finished_spans()
    assert len(arize_spans) == 1
    assert arize_spans[0].name == "call_llm"
    assert (
        arize_spans[0].instrumentation_scope.name
        == observability.ADK_INSTRUMENTATION_SCOPE
    )
    assert sum(span.attributes["llm.token_count.total"] for span in arize_spans) == 11
    native_span = next(
        span
        for span in tracing.cloud.get_finished_spans()
        if span.name.startswith("generate_content")
    )
    assert native_span.attributes["input.value"] == "native-private-content"
    assert observability.get_arize_export_diagnostics()["successful_spans"] == 1


@pytest.mark.parametrize("capture_content", [False, True])
def test_export_boundary_redacts_bypassed_content_and_error_details_on_copies_only(
    tracing, monkeypatch, capture_content
):
    monkeypatch.setenv("ARIZE_CAPTURE_CONTENT", str(capture_content).lower())
    observability.setup_arize_observability()
    private = "synthetic-private-content"
    link_context = SpanContext(
        trace_id=1, span_id=2, is_remote=True, trace_flags=TraceFlags(1)
    )
    with tracing.provider.get_tracer(
        observability.ADK_INSTRUMENTATION_SCOPE
    ).start_as_current_span(
        "call_llm",
        links=[Link(link_context, attributes={"metadata": private})],
        attributes={
            "openinference.span.kind": "LLM",
            "llm.model_name": "unchanged-model",
            "llm.token_count.total": 19,
            "gen_ai.usage.input_tokens": 10,
            "session.id": "safe-session-id",
            "input.value": private,
            "output.value": private,
            "llm.input_messages.0.message.content": private,
            "llm.output_messages.0.message.content": private,
            "llm.invocation_parameters": private,
            "tool.parameters": private,
            "gen_ai.tool.call.arguments": private,
            "gen_ai.tool.call.result": private,
            "metadata": private,
            "future.unknown.payload": private,
        },
    ) as span:
        span.set_status(Status(StatusCode.ERROR, description=private))
        span.add_event(
            "exception",
            attributes={
                "exception.type": "ValueError",
                "exception.escaped": True,
                "exception.message": private,
                "exception.stacktrace": private,
                "metadata": private,
            },
        )
        span.add_event(private, attributes={"input.value": private})
    assert observability.flush_arize_observability()
    cloud = tracing.cloud.get_finished_spans()[0]
    exported = tracing.exported.get_finished_spans()[0]
    assert cloud.context == exported.context
    assert cloud.start_time == exported.start_time
    assert cloud.end_time == exported.end_time
    assert exported.attributes["llm.model_name"] == "unchanged-model"
    assert exported.attributes["llm.token_count.total"] == 19
    assert exported.attributes["gen_ai.usage.input_tokens"] == 10
    assert exported.attributes["session.id"] == "safe-session-id"
    assert exported.status.status_code is StatusCode.ERROR
    assert exported.events[0].timestamp == cloud.events[0].timestamp
    assert exported.events[0].attributes["exception.type"] == "ValueError"
    assert exported.links[0].context == cloud.links[0].context
    assert cloud.attributes["input.value"] == private
    assert cloud.status.description == private
    assert cloud.events[0].attributes["exception.message"] == private
    assert cloud.links[0].attributes["metadata"] == private
    if capture_content:
        assert exported.attributes["input.value"] == private
        assert exported.status.description == private
        assert len(exported.events) == 2
        assert exported.events[0].attributes["exception.message"] == private
        assert exported.links[0].attributes["metadata"] == private
    else:
        assert private not in repr(dict(exported.attributes))
        assert exported.status.description is None
        assert len(exported.events) == 1
        assert exported.events[0].attributes == {
            "exception.type": "ValueError",
            "exception.escaped": True,
        }
        assert not exported.links[0].attributes
