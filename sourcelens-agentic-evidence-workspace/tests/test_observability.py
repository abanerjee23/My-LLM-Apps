from __future__ import annotations

from contextlib import contextmanager

import agents
from galileo import galileo_context

from sourcelens.observability import configure_galileo, investigation_trace


def test_galileo_is_disabled_without_complete_configuration(settings):
    unconfigured = settings.model_copy(
        update={"galileo_api_key": None, "galileo_project": None, "galileo_log_stream": None}
    )
    assert not unconfigured.galileo_enabled
    assert not configure_galileo(unconfigured)


def test_galileo_registers_the_agents_trace_processor(monkeypatch, settings):
    registered = []
    monkeypatch.setattr(agents, "set_trace_processors", lambda processors: registered.extend(processors))
    monkeypatch.setattr(galileo_context, "init", lambda **kwargs: None)
    monkeypatch.setattr(galileo_context, "get_logger_instance", lambda: object())
    configured = settings.model_copy(
        update={
            "galileo_api_key": "test-key",
            "galileo_project": "sourcelens",
            "galileo_log_stream": "log-stream-sourcelens",
        }
    )

    assert configure_galileo(configured)
    assert len(registered) == 1
    assert registered[0].__class__.__name__ == "GalileoTracingProcessor"


def test_investigation_session_contains_one_workflow_trace(monkeypatch, settings):
    events = []

    @contextmanager
    def fake_trace(name, **kwargs):
        events.append(("trace", name, kwargs))
        yield

    monkeypatch.setattr(agents, "trace", fake_trace)
    monkeypatch.setattr(
        galileo_context,
        "start_session",
        lambda **kwargs: events.append(("start_session", kwargs)) or "galileo-session-1",
    )
    monkeypatch.setattr(galileo_context, "clear_session", lambda: events.append(("clear_session",)))
    configured = settings.model_copy(
        update={
            "galileo_api_key": "test-key",
            "galileo_project": "sourcelens",
            "galileo_log_stream": "log-stream-sourcelens",
        }
    )

    with investigation_trace(configured, "investigation-1", "initial investigation") as session_id:
        assert session_id == "galileo-session-1"

    assert [event[0] for event in events] == ["start_session", "trace", "clear_session"]
    assert events[1][2]["group_id"] == "investigation-1"
