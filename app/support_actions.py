"""Durable support-action workflow shared by the agent and reviewer dashboard."""

from __future__ import annotations

import copy
import hashlib
import os
import threading
import uuid
from collections import Counter
from datetime import UTC, datetime
from statistics import median
from typing import Any, Protocol

from app import config

COLLECTION = os.getenv("SUPPORT_ACTION_COLLECTION", "support_actions")
OVERDUE_HOURS = int(os.getenv("SUPPORT_ACTION_OVERDUE_HOURS", "24"))

ACTIVE_STATUSES = {"pending", "in_review", "reopened", "needs_information"}
TRANSITIONS = {
    "pending": {"in_review", "approved", "rejected", "needs_information"},
    "in_review": {"approved", "rejected", "needs_information"},
    "approved": {"completed", "failed"},
    "rejected": {"reopened"},
    "needs_information": {"reopened"},
    "reopened": {"in_review", "approved", "rejected", "needs_information"},
    "completed": set(),
    "failed": {"reopened"},
}


class SupportActionError(RuntimeError):
    pass


class ActionNotFound(SupportActionError):
    pass


class InvalidTransition(SupportActionError):
    pass


class VersionConflict(SupportActionError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _reference(idempotency_key: str) -> str:
    seed = idempotency_key or uuid.uuid4().hex
    suffix = hashlib.sha256(seed.encode()).hexdigest()[:10].upper()
    return f"REQ-{suffix}"


def _queue(kind: str) -> str:
    return {
        "return": "Returns",
        "exchange": "Exchanges",
        "billing_adjustment": "Billing",
        "escalation": "General support",
    }.get(kind, "General support")


def _new_record(
    *,
    kind: str,
    order_id: str,
    summary: str,
    details: dict[str, Any],
    customer_id: str,
    user_id: str,
    session_id: str,
    idempotency_key: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    timestamp = _now()
    reference = _reference(idempotency_key)
    return {
        "reference": reference,
        "kind": kind,
        "assigned_queue": _queue(kind),
        "status": "pending",
        "order_id": order_id,
        "customer_id": customer_id,
        "user_id": user_id,
        "session_id": session_id,
        "summary": summary,
        "customer_request": details,
        "proposed_action": summary,
        "order_facts": context.get("order") or {},
        "policy_evidence": context.get("policy_evidence") or "",
        "assignee": None,
        "reviewer": None,
        "decision_note": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "first_decision_at": None,
        "version": 1,
        "idempotency_key": idempotency_key,
        "audit": [
            {
                "event": "created",
                "actor": "customer-support-agent",
                "at": timestamp,
                "note": "Request written successfully; no customer-impacting action taken.",
            }
        ],
    }


class SupportActionStore(Protocol):
    def create(self, **values: Any) -> dict[str, Any]: ...

    def get(self, reference: str) -> dict[str, Any]: ...

    def list(self, *, status: str = "", kind: str = "", limit: int = 500) -> list[dict[str, Any]]: ...

    def assign(self, reference: str, *, reviewer: str, assignee: str, note: str = "", expected_version: int | None = None) -> dict[str, Any]: ...

    def transition(self, reference: str, *, target: str, reviewer: str, note: str, expected_version: int | None = None) -> dict[str, Any]: ...


def _validate_version(record: dict[str, Any], expected_version: int | None) -> None:
    if expected_version is not None and record["version"] != expected_version:
        raise VersionConflict(
            f"This request changed after it was loaded (current version {record['version']})."
        )


def _assigned(record: dict[str, Any], reviewer: str, assignee: str, note: str) -> dict[str, Any]:
    if record["status"] not in {"pending", "reopened", "in_review"}:
        raise InvalidTransition(f"A {record['status']} request cannot be assigned.")
    timestamp = _now()
    updated = copy.deepcopy(record)
    updated.update(
        {
            "status": "in_review",
            "assignee": assignee,
            "reviewer": reviewer,
            "updated_at": timestamp,
            "version": record["version"] + 1,
        }
    )
    updated["audit"].append(
        {"event": "assigned", "actor": reviewer, "at": timestamp, "note": note or f"Assigned to {assignee}."}
    )
    return updated


def _transitioned(record: dict[str, Any], target: str, reviewer: str, note: str) -> dict[str, Any]:
    target = target.strip().lower()
    if target not in TRANSITIONS.get(record["status"], set()):
        raise InvalidTransition(f"Cannot move {record['status']} to {target}.")
    timestamp = _now()
    updated = copy.deepcopy(record)
    updated.update(
        {
            "status": target,
            "reviewer": reviewer,
            "decision_note": note,
            "updated_at": timestamp,
            "version": record["version"] + 1,
        }
    )
    if target in {"approved", "rejected", "needs_information"} and not updated["first_decision_at"]:
        updated["first_decision_at"] = timestamp
    updated["audit"].append(
        {"event": target, "actor": reviewer, "at": timestamp, "note": note}
    )
    return updated


class InMemorySupportActionStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = threading.RLock()

    def create(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            key = values["idempotency_key"]
            if key and key in self._idempotency:
                return self.get(self._idempotency[key])
            record = _new_record(**values)
            self._records[record["reference"]] = record
            if key:
                self._idempotency[key] = record["reference"]
            return copy.deepcopy(record)

    def get(self, reference: str) -> dict[str, Any]:
        with self._lock:
            record = self._records.get(reference.upper())
            if record is None:
                raise ActionNotFound(reference)
            return copy.deepcopy(record)

    def list(self, *, status: str = "", kind: str = "", limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            records = [copy.deepcopy(record) for record in self._records.values()]
        if status:
            records = [record for record in records if record["status"] == status]
        if kind:
            records = [record for record in records if record["kind"] == kind]
        return sorted(records, key=lambda record: record["created_at"], reverse=True)[:limit]

    def assign(self, reference: str, *, reviewer: str, assignee: str, note: str = "", expected_version: int | None = None) -> dict[str, Any]:
        with self._lock:
            current = self.get(reference)
            _validate_version(current, expected_version)
            updated = _assigned(current, reviewer, assignee, note)
            self._records[updated["reference"]] = updated
            return copy.deepcopy(updated)

    def transition(self, reference: str, *, target: str, reviewer: str, note: str, expected_version: int | None = None) -> dict[str, Any]:
        with self._lock:
            current = self.get(reference)
            _validate_version(current, expected_version)
            updated = _transitioned(current, target, reviewer, note)
            self._records[updated["reference"]] = updated
            return copy.deepcopy(updated)


class FirestoreSupportActionStore:
    def __init__(self) -> None:
        from google.cloud import firestore

        self._firestore = firestore
        self._client = firestore.Client(
            project=os.getenv("FIRESTORE_PROJECT_ID") or config.PROJECT_ID or None,
            database=os.getenv("FIRESTORE_DATABASE", "(default)"),
        )
        self._collection = self._client.collection(COLLECTION)

    def create(self, **values: Any) -> dict[str, Any]:
        from google.api_core.exceptions import AlreadyExists

        record = _new_record(**values)
        ref = self._collection.document(record["reference"])
        try:
            ref.create(record)
        except AlreadyExists:
            return self.get(record["reference"])
        return record

    def get(self, reference: str) -> dict[str, Any]:
        snapshot = self._collection.document(reference.upper()).get()
        if not snapshot.exists:
            raise ActionNotFound(reference)
        return snapshot.to_dict()

    def list(self, *, status: str = "", kind: str = "", limit: int = 500) -> list[dict[str, Any]]:
        records = [snapshot.to_dict() for snapshot in self._collection.limit(limit).stream()]
        if status:
            records = [record for record in records if record["status"] == status]
        if kind:
            records = [record for record in records if record["kind"] == kind]
        return sorted(records, key=lambda record: record["created_at"], reverse=True)

    def _mutate(self, reference: str, expected_version: int | None, change: Any) -> dict[str, Any]:
        doc_ref = self._collection.document(reference.upper())
        transaction = self._client.transaction()

        @self._firestore.transactional
        def apply(transaction):
            snapshot = doc_ref.get(transaction=transaction)
            if not snapshot.exists:
                raise ActionNotFound(reference)
            current = snapshot.to_dict()
            _validate_version(current, expected_version)
            updated = change(current)
            transaction.set(doc_ref, updated)
            return updated

        return apply(transaction)

    def assign(self, reference: str, *, reviewer: str, assignee: str, note: str = "", expected_version: int | None = None) -> dict[str, Any]:
        return self._mutate(
            reference,
            expected_version,
            lambda current: _assigned(current, reviewer, assignee, note),
        )

    def transition(self, reference: str, *, target: str, reviewer: str, note: str, expected_version: int | None = None) -> dict[str, Any]:
        return self._mutate(
            reference,
            expected_version,
            lambda current: _transitioned(current, target, reviewer, note),
        )


_store: SupportActionStore | None = None


def get_store() -> SupportActionStore:
    global _store
    if _store is None:
        backend = os.getenv("SUPPORT_ACTION_BACKEND", "").lower()
        deployed = bool(os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID") or os.getenv("K_SERVICE"))
        _store = FirestoreSupportActionStore() if backend == "firestore" or (deployed and backend != "memory") else InMemorySupportActionStore()
    return _store


def set_store(store: SupportActionStore) -> None:
    global _store
    _store = store


def reset_store() -> None:
    global _store
    _store = None


def calculate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(UTC)
    decision_hours: list[float] = []
    overdue = 0
    for record in records:
        created = datetime.fromisoformat(record["created_at"])
        if record["status"] in ACTIVE_STATUSES and (now - created).total_seconds() >= OVERDUE_HOURS * 3600:
            overdue += 1
        if record.get("first_decision_at"):
            decided = datetime.fromisoformat(record["first_decision_at"])
            decision_hours.append((decided - created).total_seconds() / 3600)
    sorted_times = sorted(decision_hours)
    p95_index = max(0, round(0.95 * len(sorted_times) + 0.5) - 1)
    decided = [r for r in records if r["status"] in {"approved", "rejected", "completed", "failed"}]
    approved = [r for r in decided if r["status"] in {"approved", "completed"}]
    return {
        "total": len(records),
        "open": sum(record["status"] in ACTIVE_STATUSES for record in records),
        "overdue": overdue,
        "by_status": dict(Counter(record["status"] for record in records)),
        "by_kind": dict(Counter(record["kind"] for record in records)),
        "median_first_decision_hours": round(median(sorted_times), 2) if sorted_times else None,
        "p95_first_decision_hours": round(sorted_times[p95_index], 2) if sorted_times else None,
        "approval_rate": round(len(approved) / len(decided), 3) if decided else None,
    }
