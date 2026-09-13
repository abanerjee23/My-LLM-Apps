from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import dashboard_api, support_actions


@pytest.fixture(autouse=True)
def _fresh_store():
    store = support_actions.InMemorySupportActionStore()
    support_actions.set_store(store)
    yield store
    support_actions.reset_store()


def _create(**overrides):
    values = {
        "kind": "return",
        "order_id": "TF-88213",
        "summary": "Review a return for damaged trainers",
        "details": {"reason": "sole split"},
        "customer_id": "C-4471",
        "user_id": "customer-1",
        "session_id": "session-1",
        "idempotency_key": "turn-1:tool-1",
        "context": {
            "order": {"status": "delivered"},
            "policy_evidence": "[tarnfield_returns_policy.pdf] Section 5",
        },
    }
    values.update(overrides)
    return support_actions.get_store().create(**values)


def test_create_is_durable_contract_and_idempotent():
    first = _create()
    second = _create()

    assert first["reference"] == second["reference"]
    assert first["status"] == "pending"
    assert first["customer_id"] == "C-4471"
    assert first["policy_evidence"]
    assert first["audit"][0]["event"] == "created"


def test_human_workflow_enforces_transitions_and_audit():
    created = _create()
    assigned = support_actions.get_store().assign(
        created["reference"],
        reviewer="reviewer@tarnfield.test",
        assignee="reviewer@tarnfield.test",
        expected_version=created["version"],
    )
    approved = support_actions.get_store().transition(
        created["reference"],
        target="approved",
        reviewer="reviewer@tarnfield.test",
        note="Policy evidence supports the return.",
        expected_version=assigned["version"],
    )
    completed = support_actions.get_store().transition(
        created["reference"],
        target="completed",
        reviewer="reviewer@tarnfield.test",
        note="Warehouse return label issued.",
        expected_version=approved["version"],
    )

    assert completed["status"] == "completed"
    assert [event["event"] for event in completed["audit"]] == [
        "created",
        "assigned",
        "approved",
        "completed",
    ]
    with pytest.raises(support_actions.InvalidTransition):
        support_actions.get_store().transition(
            created["reference"],
            target="approved",
            reviewer="reviewer@tarnfield.test",
            note="Try to reopen a closed request incorrectly.",
        )


def test_stale_dashboard_write_is_rejected():
    created = _create()
    support_actions.get_store().assign(
        created["reference"],
        reviewer="first@tarnfield.test",
        assignee="first@tarnfield.test",
        expected_version=created["version"],
    )
    with pytest.raises(support_actions.VersionConflict):
        support_actions.get_store().assign(
            created["reference"],
            reviewer="second@tarnfield.test",
            assignee="second@tarnfield.test",
            expected_version=created["version"],
        )


def test_dashboard_api_drives_the_human_workflow(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    created = _create()
    app = FastAPI()
    app.include_router(dashboard_api.router)
    client = TestClient(app)

    queue = client.get("/ops/api/actions").json()["actions"]
    assert queue[0]["reference"] == created["reference"]

    response = client.post(
        f"/ops/api/actions/{created['reference']}/decision",
        json={
            "decision": "approve",
            "note": "Evidence checked by a human reviewer.",
            "expected_version": created["version"],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_production_dashboard_requires_an_upstream_identity(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "support-ops-dashboard")
    monkeypatch.delenv("DASHBOARD_AUTH_MODE", raising=False)
    app = FastAPI()
    app.include_router(dashboard_api.router)

    assert TestClient(app).get("/ops/api/actions").status_code == 401


def test_private_cloud_run_mode_records_the_configured_reviewer(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "support-ops-dashboard")
    monkeypatch.setenv("DASHBOARD_AUTH_MODE", "cloud_run_iam")
    monkeypatch.setenv("DASHBOARD_SERVICE_REVIEWER", "reviewer@example.com")
    monkeypatch.setenv("DASHBOARD_REVIEWERS", "reviewer@example.com")
    created = _create()
    app = FastAPI()
    app.include_router(dashboard_api.router)

    response = TestClient(app).post(
        f"/ops/api/actions/{created['reference']}/assign",
        json={"assignee": "me", "expected_version": 1},
    )

    assert response.status_code == 200
    assert response.json()["assignee"] == "reviewer@example.com"


def test_metrics_cover_queue_health():
    created = _create()
    record = support_actions.get_store()._records[created["reference"]]
    record["created_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    metrics = support_actions.calculate_metrics(support_actions.get_store().list())

    assert metrics["open"] == 1
    assert metrics["overdue"] == 1
    assert metrics["by_kind"] == {"return": 1}
    action = _create()
    store = support_actions.get_store()

    assigned = store.assign(
        action["reference"], reviewer="reviewer@example.com", assignee="Asha"
    )
    approved = store.transition(
        action["reference"],
        target="approved",
        reviewer="reviewer@example.com",
        note="Policy evidence is sufficient.",
        expected_version=assigned["version"],
    )
    completed = store.transition(
        action["reference"],
        target="completed",
        reviewer="reviewer@example.com",
        note="Return label issued in the commerce system.",
        expected_version=approved["version"],
    )

    assert completed["status"] == "completed"
    assert [event["event"] for event in completed["audit"]] == [
        "created",
        "assigned",
        "approved",
        "completed",
    ]
    with pytest.raises(support_actions.InvalidTransition):
        store.transition(
            action["reference"],
            target="approved",
            reviewer="reviewer@example.com",
            note="Cannot approve completed work.",
        )


def test_stale_reviewer_write_is_rejected():
    action = _create()
    support_actions.get_store().assign(
        action["reference"], reviewer="one@example.com", assignee="One"
    )

    with pytest.raises(support_actions.VersionConflict):
        support_actions.get_store().transition(
            action["reference"],
            target="approved",
            reviewer="two@example.com",
            note="Stale browser tab",
            expected_version=action["version"],
        )


def test_metrics_include_queue_age_and_decision_time(_fresh_store):
    action = _create(idempotency_key="old")
    old = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    _fresh_store._records[action["reference"]]["created_at"] = old
    _create(kind="billing_adjustment", idempotency_key="new")

    metrics = support_actions.calculate_metrics(_fresh_store.list())

    assert metrics["total"] == 2
    assert metrics["by_status"]["pending"] == 2
    assert metrics["by_kind"]["return"] == 1
    assert metrics["overdue"] == 1
