"""Prove automatic ADK spans and Arize collector acceptance.

    uv run python scripts/arize_trace_smoke.py          # deterministic transport
    uv run python scripts/arize_trace_smoke.py --real   # one real model/RAG turn

The default uses a deterministic BaseLlm inside a real ADK Runner and executes
a read-only fixture tool. It proves framework instrumentation and transport,
not model behavior. --real runs the unchanged root agent on the final-sale
fixture, spends model credits, and reads the live policy corpus. Both modes
use isolated in-memory sessions/memory; any support requests stay in memory.
No spans are constructed manually. Collector acceptance does not prove that
the dashboard has finished indexing a trace.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from collections import Counter
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.adk.agents import LlmAgent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.memory import InMemoryMemoryService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from openinference.instrumentation import REDACTED_VALUE
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExportResult

from app import fixtures

FINAL_SALE_QUESTION = (
    "Hi, I'd like to return the Solstice Edition trainers from order TF-88213 "
    "please, they're not right."
)
REQUIRED_SPAN_KINDS = frozenset({"CHAIN", "AGENT", "LLM", "TOOL"})
ADK_SCOPE = "openinference.instrumentation.google_adk"
CONTENT_KEYS = frozenset(
    {
        "input.value",
        "output.value",
        "tool.parameters",
        "llm.invocation_parameters",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gcp.vertex.agent.llm_request",
        "gcp.vertex.agent.llm_response",
        "gcp.vertex.agent.tool_call_args",
        "gcp.vertex.agent.tool_response",
        "gcp.vertex.agent.data",
        "gen_ai.prompt",
        "gen_ai.completion",
        "gen_ai.system_instructions",
        "tool.arguments",
        "tool.result",
        "tool.input",
        "tool.output",
    }
)
CONTENT_PREFIXES = (
    "input.value.",
    "output.value.",
    "llm.invocation_parameters.",
    "llm.input_messages",
    "llm.output_messages",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.prompt.",
    "gen_ai.completion.",
    "gcp.vertex.agent.llm_request.",
    "gcp.vertex.agent.llm_response.",
    "gcp.vertex.agent.tool_call_args.",
    "gcp.vertex.agent.tool_response.",
)
OPERATIONAL_KEYS = frozenset(
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


def _eligible_adk_span(span: ReadableSpan) -> bool:
    return getattr(
        getattr(span, "instrumentation_scope", None), "name", None
    ) == ADK_SCOPE and bool((span.attributes or {}).get("openinference.span.kind"))


def privacy_report(
    spans: Sequence[ReadableSpan], *, capture_content: bool = False
) -> dict[str, Any]:
    """Check outbound payloads without exposing any content in the report.

    Native Google telemetry remains in Cloud and is not an eligible Arize span.
    With content capture disabled, exception messages/stack traces, arbitrary
    event payloads, status descriptions and link attributes must be omitted.
    """
    unexpected_scope = sum(not _eligible_adk_span(span) for span in spans)
    unredacted = sum(
        1
        for span in spans
        for key, value in (span.attributes or {}).items()
        if (key in CONTENT_KEYS or key.startswith(CONTENT_PREFIXES))
        and value != REDACTED_VALUE
    )
    unknown_fields = sum(
        1
        for span in spans
        for key, value in (span.attributes or {}).items()
        if key not in OPERATIONAL_KEYS
        and not (key in {"input.value", "output.value"} and value == REDACTED_VALUE)
        and not (
            key.startswith(("llm.token_count.", "gen_ai.usage."))
            and isinstance(value, (int, float))
        )
    )
    unsafe_events = 0
    for span in spans:
        for event in span.events or []:
            if event.name != "exception":
                unsafe_events += 1
            unsafe_events += sum(
                key not in {"exception.type", "exception.escaped"}
                and value != REDACTED_VALUE
                for key, value in (event.attributes or {}).items()
            )
    unsafe_status = sum(
        span.status.description not in (None, "", REDACTED_VALUE) for span in spans
    )
    unsafe_links = sum(
        bool(link.attributes) for span in spans for link in span.links or []
    )
    content_safe = (
        unredacted
        == unknown_fields
        == unsafe_events
        == unsafe_status
        == unsafe_links
        == 0
    )
    return {
        "capture_content": capture_content,
        "inspected_outbound_spans": len(spans),
        "unexpected_scope_spans": unexpected_scope,
        "unredacted_content_fields": unredacted,
        "unknown_attribute_fields": unknown_fields,
        "unsafe_event_fields": unsafe_events,
        "unsafe_status_descriptions": unsafe_status,
        "unsafe_link_attributes": unsafe_links,
        "message_tool_content_redacted": not capture_content and content_safe,
        "privacy_passed": unexpected_scope == 0 and (capture_content or content_safe),
    }


class _OutboundCapture:
    """Validate sanitized copies at the public SDK export boundary."""

    def __init__(self, *, capture_content: bool):
        self.capture_content = capture_content
        self.spans: list[ReadableSpan] = []

    def export(self, sdk_export, exporter, spans) -> SpanExportResult:
        self.spans.extend(spans)
        if not privacy_report(spans, capture_content=self.capture_content)[
            "privacy_passed"
        ]:
            # Reject before transport. Keep only safe aggregate failure counts
            # in the printed report; do not log offending values or exceptions.
            return SpanExportResult.FAILURE
        return sdk_export(exporter, spans)


def read_smoke_order(order_id: str) -> dict[str, Any]:
    """Read only product details from a demonstration order, without PII."""
    order = fixtures.get_order(order_id)
    return {
        "found": order is not None,
        "order_id": order_id,
        "items": order.get("items", []) if order else [],
    }


class _TransportLlm(BaseLlm):
    """Scripted model response; ADK still performs its normal model/tool flow."""

    model: str = "deterministic-transport-smoke"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        has_tool_response = any(
            part.function_response and part.function_response.name == "read_smoke_order"
            for content in llm_request.contents
            for part in content.parts or []
        )
        part = (
            types.Part(
                text="Transport smoke only: the read-only TF-88213 fixture tool ran."
            )
            if has_tool_response
            else types.Part(
                function_call=types.FunctionCall(
                    name="read_smoke_order", args={"order_id": "TF-88213"}
                )
            )
        )
        yield LlmResponse(
            content=types.Content(role="model", parts=[part]),
            partial=False,
            turn_complete=True,
        )


def build_transport_agent() -> LlmAgent:
    return LlmAgent(
        name="arize_transport_smoke",
        model=_TransportLlm(),
        instruction="Read the demonstration order and report transport smoke completion.",
        tools=[read_smoke_order],
        mode="chat",
    )


async def run_workflow(*, real: bool, timeout_seconds: float = 120) -> dict[str, Any]:
    """Run one bounded turn; this function does not configure tracing/export."""
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 120:
        raise ValueError("Smoke timeout must be between 1 and 120 seconds.")
    if real:
        from app.agents import build_root_agent

        agent = build_root_agent()
    else:
        agent = build_transport_agent()

    run_id = f"arize-smoke-{uuid.uuid4().hex}"
    runner = Runner(
        app_name="arize_smoke",
        agent=agent,
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        auto_create_session=True,
    )
    reply: list[str] = []
    tool_calls: list[str] = []
    started = time.monotonic()
    # Isolated synthetic session and memory scope; no action tools exist.
    try:
        async with asyncio.timeout(timeout_seconds):
            async for event in runner.run_async(
                user_id=run_id,
                session_id=run_id,
                new_message=types.Content(
                    role="user", parts=[types.Part(text=FINAL_SALE_QUESTION)]
                ),
                run_config=RunConfig(
                    streaming_mode=StreamingMode.NONE,
                    max_llm_calls=6,
                ),
            ):
                for part in (event.content.parts or []) if event.content else []:
                    if part.function_call and not event.partial:
                        tool_calls.append(part.function_call.name)
                if event.author == agent.name and event.is_final_response():
                    text_parts = [
                        part.text
                        for part in (
                            (event.content.parts or []) if event.content else []
                        )
                        if part.text and not part.thought
                    ]
                    # Final content replaces any partial tokens; do not duplicate
                    # streamed text or display reasoning/tool payloads.
                    if text_parts:
                        reply = text_parts
        return {
            "mode": "real_model" if real else "deterministic_transport",
            "real_model_check": real,
            "run_id": run_id,
            "question": FINAL_SALE_QUESTION,
            "reply": "".join(reply).strip(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "root_tool_calls": tool_calls,
        }
    finally:
        await runner.close()


def span_report(
    spans: Sequence[ReadableSpan],
    *,
    capture_content: bool = False,
    require_tools: bool = True,
) -> dict[str, Any]:
    """Summarize real framework spans, without prompts, raw tools, or secrets."""
    ai_spans = [span for span in spans if _eligible_adk_span(span)]
    kinds = Counter(
        str(span.attributes["openinference.span.kind"]) for span in ai_spans
    )
    llm_spans = [
        span for span in ai_spans if span.attributes["openinference.span.kind"] == "LLM"
    ]
    return {
        **privacy_report(spans, capture_content=capture_content),
        "span_count": len(ai_spans),
        "span_kinds": dict(sorted(kinds.items())),
        "required_span_kinds_present": (
            REQUIRED_SPAN_KINDS if require_tools else REQUIRED_SPAN_KINDS - {"TOOL"}
        )
        <= kinds.keys(),
        "model_calls": len(llm_spans),
        "models": sorted(
            {
                str(span.attributes["llm.model_name"])
                for span in llm_spans
                if "llm.model_name" in span.attributes
            }
        ),
        "prompt_tokens": sum(
            int(span.attributes.get("llm.token_count.prompt", 0)) for span in llm_spans
        ),
        "output_tokens": sum(
            int(span.attributes.get("llm.token_count.completion", 0))
            for span in llm_spans
        ),
        "trace_ids": sorted({f"{span.context.trace_id:032x}" for span in ai_spans}),
        "spans": [
            {
                "name": span.name,
                "kind": span.attributes["openinference.span.kind"],
                "status": span.status.status_code.name,
                "duration_ms": round((span.end_time - span.start_time) / 1e6, 3),
            }
            for span in ai_spans
        ],
    }


def export_report(
    before: dict[str, Any], after: dict[str, Any], *, flush_completed: bool
) -> dict[str, Any]:
    """Only a successful exporter result proves collector acceptance."""
    counters = {
        key: int(after.get(key, 0)) - int(before.get(key, 0))
        for key in (
            "attempted_batches",
            "successful_batches",
            "failed_batches",
            "attempted_spans",
            "successful_spans",
        )
    }
    accepted = (
        flush_completed
        and counters["successful_spans"] > 0
        and counters["failed_batches"] == 0
        and counters["successful_spans"] == counters["attempted_spans"]
    )
    return {
        **counters,
        "flush_completed": flush_completed,
        "collector_accepted": accepted,
        "dashboard_verified": False,
    }


async def main(*, real: bool, timeout_seconds: float = 120) -> int:
    from arize.otel import HTTPSpanExporter

    capture = _OutboundCapture(
        capture_content=os.getenv("ARIZE_CAPTURE_CONTENT", "false").strip().lower()
        in {"true", "1", "yes", "on"},
    )
    sdk_export = HTTPSpanExporter.export

    def checked_export(exporter, spans):
        return capture.export(sdk_export, exporter, spans)

    # The production helper sanitizes exported copies. Inspect those copies,
    # rather than the original Cloud spans seen by a provider-level processor.
    with patch.object(HTTPSpanExporter, "export", checked_export):
        return await _run_smoke(
            real=real, timeout_seconds=timeout_seconds, capture=capture
        )


async def _run_smoke(
    *, real: bool, timeout_seconds: float, capture: _OutboundCapture
) -> int:
    from app.app_utils.observability import (
        flush_arize_observability,
        get_arize_export_diagnostics,
        setup_arize_observability,
        shutdown_arize_observability,
    )

    try:
        if not setup_arize_observability():
            print(
                json.dumps(
                    {"error": "Enable ARIZE_ENABLED and configure Arize credentials."}
                )
            )
            return 1
        before = get_arize_export_diagnostics()
        try:
            workflow = await run_workflow(real=real, timeout_seconds=timeout_seconds)
        except Exception as exc:
            workflow = {
                "mode": "real_model" if real else "deterministic_transport",
                "real_model_check": False,
                "error": "ADK smoke turn did not complete.",
                "error_type": type(exc).__name__,
                "reply": "",
            }
        flushed = await asyncio.to_thread(flush_arize_observability)
        exported = export_report(
            before, get_arize_export_diagnostics(), flush_completed=flushed
        )
        generated = span_report(
            capture.spans,
            capture_content=capture.capture_content,
            require_tools=not real,
        )
        print(json.dumps({**workflow, **generated, "export": exported}, indent=2))
        return int(
            not (
                workflow["reply"]
                and generated["required_span_kinds_present"]
                and generated["privacy_passed"]
                and exported["collector_accepted"]
            )
        )
    except Exception as exc:
        # Client/transport exceptions can include headers; report the class only.
        print(
            json.dumps(
                {
                    "error": "Arize smoke setup or verification failed.",
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1
    finally:
        await asyncio.to_thread(shutdown_arize_observability)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real", action="store_true", help="Spend credits on one real root-agent turn."
    )
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(real=args.real, timeout_seconds=args.timeout_seconds)))
