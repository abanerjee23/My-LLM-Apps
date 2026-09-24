from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class InvestigationStatus(StrEnum):
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class Decision(StrEnum):
    ACCEPTED = "accepted"
    EDITED_ACCEPTED = "edited_accepted"
    REJECTED = "rejected"


class Evidence(BaseModel):
    evidence_id: str
    kind: str
    source_id: str
    title: str
    excerpt: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    finding_id: str
    title: str
    observation: str
    interpretation: str
    next_step: str
    evidence_ids: list[str]
    confidence: str = "medium"


class Artifact(BaseModel):
    artifact_id: str
    kind: str
    title: str
    data: dict[str, Any]
    source_query_id: str | None = None


class InvestigationEvent(BaseModel):
    sequence: int
    event_type: str
    title: str
    detail: str
    status: str = "complete"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class InvestigationRequest(BaseModel):
    source_id: str | None = Field(default=None, max_length=200)
    brief: str = Field(min_length=8, max_length=2000)


class RefinementRequest(BaseModel):
    direction: str = Field(min_length=3, max_length=1000)


class SourceConnectionRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["bigquery", "database", "rest_api"]
    project: str | None = Field(default=None, max_length=200)
    location: str = Field(default="US", max_length=100)
    endpoint: str | None = Field(default=None, max_length=500)
    dataset: str | None = Field(default=None, max_length=200)


class DataSource(BaseModel):
    source_id: str
    name: str
    kind: str
    status: Literal["connected", "configured", "processing", "failed"] = "connected"
    record_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Investigation(BaseModel):
    investigation_id: str
    title: str
    brief: str
    status: InvestigationStatus
    scope: dict[str, Any] = Field(default_factory=dict)
    executive_summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    events: list[InvestigationEvent] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ReviewRequest(BaseModel):
    decision: Decision
    edited_summary: str | None = None
    note: str | None = None
    accuracy_rating: int | None = Field(default=None, ge=1, le=5)
    usefulness_rating: int | None = Field(default=None, ge=1, le=5)


class NotebookEntry(BaseModel):
    entry_id: str
    investigation_id: str
    revision: int
    title: str
    saved_at: datetime
    decision: Decision
    summary: str
    brief_snapshot: dict[str, Any]
    note: str | None = None
    accuracy_rating: int | None = None
    usefulness_rating: int | None = None
