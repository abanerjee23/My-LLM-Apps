"""No-network checks of ADK-generated spans and honest export reporting."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from arize.otel import HTTPSpanExporter
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from scripts import arize_trace_smoke as smoke


def _outbound_span(*, attributes=None, events=(), description=None, links=(), scope=smoke.ADK_SCOPE):
    """Metadata fake for validation boundaries; not an instrumentation proof."""
    return SimpleNamespace(
        attributes={"openinference.span.kind": "LLM", **(attributes or {})},
        instrumentation_scope=SimpleNamespace(name=scope),
        events=events, links=links, status=SimpleNamespace(description=description),
    )


@pytest.mark.parametrize("key", [
    "input.value", "output.value", "llm.invocation_parameters",
    "llm.input_messages.0.message.content", "llm.output_messages.0.message.content",
    "tool.parameters", "gen_ai.tool.call.arguments", "gcp.vertex.agent.llm_request",
    "metadata", "customer-private-field",
])
def test_capture_disabled_rejects_payload_fields_without_exposing_values(key):
    report = smoke.privacy_report([_outbound_span(attributes={key: "private-customer-data"})])
    assert report["privacy_passed"] is False
    assert "private-customer-data" not in repr(report)


@pytest.mark.parametrize("key", ["exception.message", "exception.stacktrace", "message", "tool.arguments"])
def test_capture_disabled_rejects_exception_event_payloads(key):
    event = SimpleNamespace(name="exception", attributes={key: "private-error-details"})
    report = smoke.privacy_report([_outbound_span(events=[event])])
    assert report["unsafe_event_fields"] == 1
    assert report["privacy_passed"] is False
    assert "private-error-details" not in repr(report)


def test_capture_disabled_rejects_error_description_nonexception_events_and_links():
    span = _outbound_span(
        description="RuntimeError: private-error-details",
        events=[SimpleNamespace(name="gen_ai.user.message", attributes={})],
        links=[SimpleNamespace(attributes={"customer": "private-customer-data"})],
    )
    report = smoke.privacy_report([span])
    assert report["unsafe_status_descriptions"] == 1
    assert report["unsafe_event_fields"] == 1
    assert report["unsafe_link_attributes"] == 1
    assert report["privacy_passed"] is False
    assert "private-" not in repr(report)


def test_safe_redacted_errors_keep_only_operational_metadata():
    span = _outbound_span(
        attributes={"input.value": smoke.REDACTED_VALUE, "llm.token_count.total": 12},
        events=[SimpleNamespace(name="exception", attributes={"exception.type": "RuntimeError", "exception.escaped": True})],
        links=[SimpleNamespace(attributes={})],
    )
    assert smoke.privacy_report([span])["privacy_passed"] is True


def test_explicit_capture_opt_in_allows_content_but_never_native_duplicates():
    span = _outbound_span(attributes={"input.value": "explicitly-enabled-content"})
    assert smoke.privacy_report([span], capture_content=True)["privacy_passed"] is True
    span.instrumentation_scope.name = "opentelemetry.instrumentation.google_genai"
    report = smoke.privacy_report([span], capture_content=True)
    assert report["unexpected_scope_spans"] == 1
    assert report["privacy_passed"] is False


def test_outbound_guard_rejects_unsafe_batch_before_sdk_transport():
    capture = smoke._OutboundCapture(capture_content=False)
    called = []

    def sdk_export(exporter, spans):
        called.append(spans)
        return SpanExportResult.SUCCESS

    bad = _outbound_span(description="RuntimeError: private-error-details")
    assert capture.export(sdk_export, object(), [bad]) is SpanExportResult.FAILURE
    assert called == []
    good = _outbound_span()
    assert capture.export(sdk_export, object(), [good]) is SpanExportResult.SUCCESS
    assert called == [[good]]


@pytest.mark.asyncio
async def test_smoke_fails_on_outbound_privacy_even_if_export_counters_claim_success(monkeypatch, capsys):
    from app.app_utils import observability

    spans = []
    for index, kind in enumerate(smoke.REQUIRED_SPAN_KINDS):
        span = _outbound_span(attributes={"openinference.span.kind": kind})
        span.name = f"automatic_{kind}"
        span.context = SimpleNamespace(trace_id=1)
        span.start_time, span.end_time = index, index + 1
        span.status.status_code = StatusCode.OK
        spans.append(span)
    spans[0].status.description = "private-error-details"
    monkeypatch.setenv("ARIZE_CAPTURE_CONTENT", "false")
    monkeypatch.setattr(observability, "setup_arize_observability", lambda: True)
    monkeypatch.setattr(observability, "flush_arize_observability", lambda: True)
    monkeypatch.setattr(observability, "shutdown_arize_observability", lambda: None)
    snapshots = iter([{}, {"attempted_spans": 4, "successful_spans": 4}])
    monkeypatch.setattr(observability, "get_arize_export_diagnostics", lambda: next(snapshots))

    def reject_sdk_network(*args):
        raise AssertionError("Unsafe payload must never reach the real SDK transport.")

    monkeypatch.setattr(HTTPSpanExporter, "export", reject_sdk_network)

    async def completed_workflow(**kwargs):
        assert HTTPSpanExporter.export(object(), spans) is SpanExportResult.FAILURE
        return {"reply": "Finished."}

    monkeypatch.setattr(smoke, "run_workflow", completed_workflow)
    assert await smoke.main(real=False) == 1
    output = capsys.readouterr().out
    assert '"privacy_passed": false' in output
    assert "private-error-details" not in output


@pytest.mark.asyncio
async def test_adk_generates_agent_model_and_tool_spans_without_manual_spans():
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor

    provider = TracerProvider()
    collector = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    instrumentor = GoogleADKInstrumentor()
    assert not instrumentor.is_instrumented_by_opentelemetry
    instrumentor.instrument(tracer_provider=provider)
    try:
        result = await smoke.run_workflow(real=False, timeout_seconds=5)
        report = smoke.span_report(collector.get_finished_spans())
    finally:
        instrumentor.uninstrument()
        provider.shutdown()

    assert result["real_model_check"] is False
    assert result["mode"] == "deterministic_transport"
    assert result["reply"].startswith("Transport smoke only:")
    assert result["root_tool_calls"] == ["read_smoke_order"]
    assert result["isolated_support_requests"] == 0
    assert report["required_span_kinds_present"] is True
    assert report["span_kinds"]["LLM"] == 2
    assert report["model_calls"] == 2
    assert report["models"] == ["deterministic-transport-smoke"]
    assert report["prompt_tokens"] == report["output_tokens"] == 0
    assert report["span_kinds"]["TOOL"] >= 1
    assert len(report["trace_ids"]) == 1
    assert all(span["duration_ms"] >= 0 for span in report["spans"])


@pytest.mark.parametrize("timeout", [0, -1, 121, float("nan"), float("inf")])
@pytest.mark.asyncio
async def test_invalid_timeout_is_rejected_before_a_turn(timeout):
    with pytest.raises(ValueError, match="between 1 and 120"):
        await smoke.run_workflow(real=False, timeout_seconds=timeout)


def test_empty_successful_flush_is_not_export_proof():
    report = smoke.export_report({}, {}, flush_completed=True)
    assert report["collector_accepted"] is False
    assert report["dashboard_verified"] is False


def test_success_is_based_on_only_new_successful_exported_spans():
    report = smoke.export_report(
        {"successful_spans": 4, "attempted_spans": 4, "successful_batches": 1},
        {"successful_spans": 12, "attempted_spans": 12, "successful_batches": 2},
        flush_completed=True,
    )
    assert report["successful_spans"] == 8
    assert report["collector_accepted"] is True
    assert report["dashboard_verified"] is False


@pytest.mark.parametrize(
    ("after", "flushed"),
    [
        ({"successful_spans": 8, "attempted_spans": 8}, False),
        ({"successful_spans": 8, "attempted_spans": 9}, True),
        ({"successful_spans": 8, "attempted_spans": 8, "failed_batches": 1}, True),
    ],
)
def test_partial_or_failed_export_is_not_reported_as_accepted(after, flushed):
    assert smoke.export_report({}, after, flush_completed=flushed)["collector_accepted"] is False


def test_read_only_smoke_fixture_excludes_customer_and_payment_data():
    order = smoke.read_smoke_order("TF-88213")
    assert order["found"] is True
    assert set(order) == {"found", "order_id", "items"}
    assert "Solstice" in str(order["items"])
    assert smoke.read_smoke_order("TF-missing")["found"] is False


@pytest.mark.asyncio
async def test_production_helper_redacts_automatic_nested_agent_and_tool_content(monkeypatch):
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor

    from app import agents
    from app.app_utils import observability

    class RoutingLlm(BaseLlm):
        model: str = "deterministic-router"

        async def generate_content_async(self, llm_request, stream=False):
            delegated = any(
                part.function_response
                and part.function_response.name == "returns_exchanges"
                for content in llm_request.contents
                for part in content.parts or []
            )
            part = types.Part(text="Private root answer.") if delegated else types.Part(
                function_call=types.FunctionCall(
                    name="returns_exchanges",
                    args={"request": smoke.FINAL_SALE_QUESTION},
                )
            )
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    def nested_agent():
        specialist = smoke.build_transport_agent()
        specialist.name = "returns_exchanges"
        return LlmAgent(
            name="customer_service", model=RoutingLlm(),
            instruction="Delegate to the returns specialist.",
            tools=[AgentTool(agent=specialist)],
        )

    monkeypatch.setattr(agents, "build_root_agent", nested_agent)
    monkeypatch.setenv("ARIZE_ENABLED", "true")
    monkeypatch.setenv("ARIZE_CAPTURE_CONTENT", "false")
    monkeypatch.setenv("ARIZE_API_KEY", "test-only-key")
    monkeypatch.setenv("ARIZE_SPACE_ID", "test-space")
    monkeypatch.setattr(observability, "_state", None)
    monkeypatch.setattr(observability.atexit, "register", lambda callback: None)
    provider = TracerProvider()
    collector = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    exported = InMemorySpanExporter()
    monkeypatch.setattr(observability.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(observability, "_create_exporter", lambda settings: exported)
    instrumentor = GoogleADKInstrumentor()
    assert not instrumentor.is_instrumented_by_opentelemetry
    try:
        assert observability.setup_arize_observability()
        result = await smoke.run_workflow(real=True, timeout_seconds=5)
        assert observability.flush_arize_observability()
        generated = collector.get_finished_spans()
        report = smoke.span_report(exported.get_finished_spans())
        assert result["reply"] == "Private root answer."
        assert report["required_span_kinds_present"]
        assert report["span_kinds"]["AGENT"] == 2
        assert report["span_kinds"]["TOOL"] == 2
        assert report["message_tool_content_redacted"]
        assert "TF-88213" not in repr([span.attributes for span in generated])
        assert "Private root answer." not in repr([span.attributes for span in generated])
        assert "test-only-key" not in repr([span.attributes for span in generated])
        assert len(exported.get_finished_spans()) == report["span_count"]
        assert observability.get_arize_export_diagnostics()["successful_spans"] == report["span_count"]
    finally:
        observability.shutdown_arize_observability()
        if instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.uninstrument()
        provider.shutdown()


@pytest.mark.asyncio
async def test_production_helper_sanitizes_real_adk_generated_failure_spans(monkeypatch):
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor

    from app.app_utils import observability

    class FailingLlm(BaseLlm):
        model: str = "deterministic-failure"

        async def generate_content_async(self, llm_request, stream=False):
            raise RuntimeError("private-customer-error-details")
            yield  # pragma: no cover - retain ADK's async-generator contract

    monkeypatch.setattr(smoke, "build_transport_agent", lambda: LlmAgent(name="failure_smoke", model=FailingLlm()))
    monkeypatch.setenv("ARIZE_ENABLED", "true")
    monkeypatch.setenv("ARIZE_CAPTURE_CONTENT", "false")
    monkeypatch.setenv("ARIZE_API_KEY", "test-only-key")
    monkeypatch.setenv("ARIZE_SPACE_ID", "test-space")
    monkeypatch.setattr(observability, "_state", None)
    monkeypatch.setattr(observability.atexit, "register", lambda callback: None)
    provider = TracerProvider()
    originals = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(originals))
    exported = InMemorySpanExporter()
    monkeypatch.setattr(observability.trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(observability, "_create_exporter", lambda settings: exported)
    instrumentor = GoogleADKInstrumentor()
    assert not instrumentor.is_instrumented_by_opentelemetry
    try:
        assert observability.setup_arize_observability()
        with pytest.raises(RuntimeError, match="private-customer-error-details"):
            await smoke.run_workflow(real=False, timeout_seconds=5)
        assert observability.flush_arize_observability()
        spans = exported.get_finished_spans()
        assert spans
        assert any(span.status.status_code is StatusCode.ERROR for span in spans)
        assert smoke.privacy_report(spans)["privacy_passed"]
        assert "private-customer-error-details" not in repr([(span.attributes, span.events, span.status.description) for span in spans])
        assert any(span.status.description or span.events for span in originals.get_finished_spans())
    finally:
        observability.shutdown_arize_observability()
        if instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.uninstrument()
        provider.shutdown()
