"""Optional Galileo tracing for OpenAI Agents SDK runs.

The SDK processor captures agent generations, handoffs, and tool spans automatically.
It is deliberately optional: an observability outage or absent credentials must never
prevent an investigation from running.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from .config import Settings

logger = logging.getLogger(__name__)


def configure_galileo(settings: Settings) -> bool:
    """Install Galileo as the OpenAI Agents SDK trace processor when configured."""
    if not settings.galileo_enabled:
        return False
    try:
        from agents import set_trace_processors
        from galileo import galileo_context
        from galileo.handlers.openai_agents import GalileoTracingProcessor

        # Initialise the SDK's context once for this process. The agent processor
        # then exports into this project/log-stream without per-call trace code.
        galileo_context.init(
            project=settings.galileo_project,
            log_stream=settings.galileo_log_stream,
        )
        logger_instance = galileo_context.get_logger_instance()
        set_trace_processors([GalileoTracingProcessor(galileo_logger=logger_instance)])
    except Exception:
        logger.exception("Galileo tracing could not be configured; continuing without export")
        return False
    logger.info(
        "Galileo tracing enabled for project=%s log_stream=%s",
        settings.galileo_project,
        settings.galileo_log_stream,
    )
    return True


@contextmanager
def investigation_trace(
    settings: Settings,
    investigation_id: str,
    action: str,
    existing_session_id: str | None = None,
) -> Iterator[str | None]:
    """Make an investigation a Galileo session and a user action one complete trace."""
    if not settings.galileo_enabled:
        yield None
        return

    from agents import trace
    from galileo import galileo_context

    if existing_session_id:
        galileo_context.set_session(existing_session_id)
        session_id = existing_session_id
    else:
        session_id = galileo_context.start_session(
            name="SourceLens investigation",
            external_id=investigation_id,
            metadata={"investigation_id": investigation_id},
        )
    try:
        with trace(
            f"SourceLens · {action}",
            group_id=investigation_id,
            metadata={
                "investigation_id": investigation_id,
                "galileo_session_id": session_id,
                "action": action,
            },
        ):
            yield session_id
    finally:
        galileo_context.clear_session()
