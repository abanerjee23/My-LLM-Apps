from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, text

from .models import (
    DataSource,
    Investigation,
    NotebookEntry,
    ReviewRequest,
    SourceVersion,
    utc_now,
)

# Dedicated owner for existing portfolio data and the shared, read-only starter source.
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


class AppStore:
    """Application state in PostgreSQL. Every read that serves a user takes the owner.

    Owners come from verified identity tokens (see auth.py), never from browser input.
    System-owned sources are shared read-only with every user.
    """

    def __init__(self, engine: Engine):
        self.engine = engine

    # Users -----------------------------------------------------------------

    def upsert_user(self, firebase_uid: str, email: str | None, display_name: str | None) -> str:
        with self.engine.begin() as db:
            row = db.execute(
                text(
                    """
                    INSERT INTO users (user_id, firebase_uid, email, display_name, created_at, last_seen_at)
                    VALUES (CAST(:user_id AS UUID), :uid, :email, :name, now(), now())
                    ON CONFLICT (firebase_uid) DO UPDATE SET
                      email = EXCLUDED.email,
                      display_name = EXCLUDED.display_name,
                      last_seen_at = now()
                    WHERE users.last_seen_at < now() - interval '5 minutes'
                    RETURNING user_id
                    """
                ),
                {"user_id": str(uuid4()), "uid": firebase_uid, "email": email, "name": display_name},
            ).first()
            if row is None:  # Seen recently; nothing to update.
                row = db.execute(
                    text("SELECT user_id FROM users WHERE firebase_uid = :uid"),
                    {"uid": firebase_uid},
                ).first()
        return str(row[0])

    def upsert_google_user(self, subject: str, email: str, display_name: str | None) -> str:
        """Keep the owner's existing data when moving from Firebase to direct Google identity."""
        identity = f"google:{subject}"
        with self.engine.begin() as db:
            existing = db.execute(
                text("SELECT user_id FROM users WHERE lower(email) = lower(:email) LIMIT 1"),
                {"email": email},
            ).first()
            if existing:
                db.execute(
                    text(
                        """
                        UPDATE users SET display_name = :name, last_seen_at = now()
                        WHERE user_id = :user_id
                        """
                    ),
                    {"user_id": existing[0], "name": display_name},
                )
                return str(existing[0])
            row = db.execute(
                text(
                    """
                    INSERT INTO users (user_id, firebase_uid, email, display_name, created_at, last_seen_at)
                    VALUES (CAST(:user_id AS UUID), :identity, :email, :name, now(), now())
                    ON CONFLICT (firebase_uid) DO UPDATE SET
                      email = EXCLUDED.email, display_name = EXCLUDED.display_name, last_seen_at = now()
                    RETURNING user_id
                    """
                ),
                {
                    "user_id": str(uuid4()),
                    "identity": identity,
                    "email": email,
                    "name": display_name,
                },
            ).first()
        return str(row[0])

    def create_auth_session(self, token_hash: str, user_id: str, expires_at: datetime) -> None:
        with self.engine.begin() as db:
            db.execute(
                text(
                    """
                    INSERT INTO auth_sessions (token_hash, owner_user_id, expires_at)
                    VALUES (:token_hash, CAST(:user_id AS UUID), :expires_at)
                    """
                ),
                {"token_hash": token_hash, "user_id": user_id, "expires_at": expires_at},
            )

    def auth_session_user(self, token_hash: str) -> dict[str, Any] | None:
        with self.engine.connect() as db:
            row = db.execute(
                text(
                    """
                    SELECT u.user_id, u.firebase_uid, u.email, u.display_name
                    FROM auth_sessions s
                    JOIN users u ON u.user_id = s.owner_user_id
                    WHERE s.token_hash = :token_hash
                      AND s.revoked_at IS NULL AND s.expires_at > now()
                    """
                ),
                {"token_hash": token_hash},
            ).mappings().first()
        return dict(row) if row else None

    def revoke_auth_session(self, token_hash: str) -> None:
        with self.engine.begin() as db:
            db.execute(
                text(
                    "UPDATE auth_sessions SET revoked_at = now() "
                    "WHERE token_hash = :token_hash AND revoked_at IS NULL"
                ),
                {"token_hash": token_hash},
            )

    def delete_user_data(self, user_id: str) -> None:
        """Remove every row owned by a user. Scoped object-store cleanup is separate."""
        if user_id == SYSTEM_USER_ID:
            raise ValueError("The system owner cannot be deleted")
        with self.engine.begin() as db:
            for statement in (
                "DELETE FROM notebook_entries WHERE owner_user_id = CAST(:u AS UUID)",
                "DELETE FROM usage_ledger WHERE owner_user_id = CAST(:u AS UUID)",
                "DELETE FROM investigations WHERE owner_user_id = CAST(:u AS UUID)",
                "DELETE FROM data_sources WHERE owner_user_id = CAST(:u AS UUID)",
                "DELETE FROM workspace_members WHERE user_id = CAST(:u AS UUID)",
                "DELETE FROM users WHERE user_id = CAST(:u AS UUID)",
            ):
                db.execute(text(statement), {"u": user_id})

    # Investigations ----------------------------------------------------------

    def create_investigation(self, investigation: Investigation, owner_user_id: str) -> None:
        """Owner is recorded once, before any agent runs, and never changes."""
        with self.engine.begin() as db:
            db.execute(
                text(
                    """
                    INSERT INTO investigations (investigation_id, owner_user_id, payload, updated_at)
                    VALUES (:id, CAST(:owner AS UUID), CAST(:payload AS JSONB), :updated_at)
                    """
                ),
                {
                    "id": investigation.investigation_id,
                    "owner": owner_user_id,
                    "payload": investigation.model_dump_json(),
                    "updated_at": investigation.updated_at,
                },
            )

    def save_investigation(self, investigation: Investigation) -> None:
        """Update an investigation already loaded through an owner-scoped read."""
        with self.engine.begin() as db:
            result = db.execute(
                text(
                    """
                    UPDATE investigations SET payload = CAST(:payload AS JSONB), updated_at = :updated_at
                    WHERE investigation_id = :id
                    """
                ),
                {
                    "id": investigation.investigation_id,
                    "payload": investigation.model_dump_json(),
                    "updated_at": investigation.updated_at,
                },
            )
        if result.rowcount != 1:
            raise LookupError(f"Investigation {investigation.investigation_id} does not exist")

    def get_investigation(self, investigation_id: str, owner_user_id: str) -> Investigation | None:
        with self.engine.connect() as db:
            row = db.execute(
                text(
                    """
                    SELECT payload FROM investigations
                    WHERE investigation_id = :id AND owner_user_id = CAST(:owner AS UUID)
                    """
                ),
                {"id": investigation_id, "owner": owner_user_id},
            ).first()
        return Investigation.model_validate(row[0]) if row else None

    def list_investigations(self, owner_user_id: str) -> list[Investigation]:
        with self.engine.connect() as db:
            rows = db.execute(
                text(
                    """
                    SELECT payload FROM investigations
                    WHERE owner_user_id = CAST(:owner AS UUID)
                    ORDER BY updated_at DESC LIMIT 100
                    """
                ),
                {"owner": owner_user_id},
            ).all()
        return [Investigation.model_validate(row[0]) for row in rows]

    # Notebook ----------------------------------------------------------------

    def save_review(
        self, investigation: Investigation, review: ReviewRequest, owner_user_id: str
    ) -> NotebookEntry:
        with self.engine.begin() as db:
            # Serialise revisions per investigation; the row lock also re-checks ownership.
            owned = db.execute(
                text(
                    """
                    SELECT 1 FROM investigations
                    WHERE investigation_id = :id AND owner_user_id = CAST(:owner AS UUID)
                    FOR UPDATE
                    """
                ),
                {"id": investigation.investigation_id, "owner": owner_user_id},
            ).first()
            if not owned:
                raise LookupError("Investigation not found")
            revision = int(
                db.execute(
                    text(
                        "SELECT COALESCE(MAX(revision), 0) + 1 FROM notebook_entries WHERE investigation_id = :id"
                    ),
                    {"id": investigation.investigation_id},
                ).scalar_one()
            )
            entry = NotebookEntry(
                entry_id=str(uuid4()),
                investigation_id=investigation.investigation_id,
                owner_user_id=owner_user_id,
                revision=revision,
                title=investigation.title,
                saved_at=utc_now(),
                decision=review.decision,
                summary=review.edited_summary or investigation.executive_summary,
                brief_snapshot=investigation.model_dump(mode="json"),
                note=review.note,
                accuracy_rating=review.accuracy_rating,
                usefulness_rating=review.usefulness_rating,
            )
            db.execute(
                text(
                    """
                    INSERT INTO notebook_entries
                      (entry_id, investigation_id, owner_user_id, revision, saved_at, payload)
                    VALUES (:entry_id, :investigation_id, CAST(:owner AS UUID), :revision, :saved_at,
                            CAST(:payload AS JSONB))
                    """
                ),
                {
                    "entry_id": entry.entry_id,
                    "investigation_id": entry.investigation_id,
                    "owner": owner_user_id,
                    "revision": entry.revision,
                    "saved_at": entry.saved_at,
                    "payload": entry.model_dump_json(),
                },
            )
        return entry

    def list_notebook(self, owner_user_id: str) -> list[NotebookEntry]:
        with self.engine.connect() as db:
            rows = db.execute(
                text(
                    """
                    SELECT payload FROM notebook_entries
                    WHERE owner_user_id = CAST(:owner AS UUID)
                    ORDER BY saved_at DESC
                    """
                ),
                {"owner": owner_user_id},
            ).all()
        return [NotebookEntry.model_validate(row[0]) for row in rows]

    def export_notebook(self, owner_user_id: str) -> str:
        return json.dumps(
            [entry.model_dump(mode="json") for entry in self.list_notebook(owner_user_id)], indent=2
        )

    # Usage (budget and run cap stay global for now; per-user limits are deferred) ---

    def add_usage(
        self,
        investigation_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
        duration_ms: int | None = None,
    ) -> None:
        with self.engine.begin() as db:
            db.execute(
                text(
                    """
                    INSERT INTO usage_ledger
                      (investigation_id, owner_user_id, model, input_tokens, output_tokens,
                       estimated_cost, duration_ms, created_at)
                    SELECT :id,
                      (SELECT owner_user_id FROM investigations WHERE investigation_id = :id),
                      :model, :input_tokens, :output_tokens, :cost, :duration_ms, now()
                    """
                ),
                {
                    "id": investigation_id,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost": estimated_cost,
                    "duration_ms": duration_ms,
                },
            )

    def count_live_runs(self) -> int:
        with self.engine.connect() as db:
            return int(
                db.execute(text("SELECT COUNT(DISTINCT investigation_id) FROM usage_ledger")).scalar_one()
            )

    def usage_cost(self) -> float:
        with self.engine.connect() as db:
            return float(
                db.execute(text("SELECT COALESCE(SUM(estimated_cost), 0) FROM usage_ledger")).scalar_one()
            )

    # Sources -----------------------------------------------------------------

    def save_source(
        self,
        source: DataSource,
        owner_user_id: str,
        records: list[dict] | None = None,
        version: SourceVersion | None = None,
    ) -> None:
        """Create or update a source. With records, also store them as a new version."""
        if records is not None and version is None:
            version = SourceVersion(
                version_id=f"v-{uuid4()}",
                source_id=source.source_id,
                record_count=len(records),
                sha256=source.metadata.get("sha256"),
            )
        if version is not None:
            source.version_id = version.version_id
        with self.engine.begin() as db:
            result = db.execute(
                text(
                    """
                    INSERT INTO data_sources
                      (source_id, owner_user_id, current_version_id, payload, created_at)
                    VALUES (:id, CAST(:owner AS UUID), :version_id, CAST(:payload AS JSONB), :created_at)
                    ON CONFLICT (source_id) DO UPDATE SET
                      payload = EXCLUDED.payload,
                      current_version_id = COALESCE(EXCLUDED.current_version_id,
                                                    data_sources.current_version_id)
                    WHERE data_sources.owner_user_id = EXCLUDED.owner_user_id
                    """
                ),
                {
                    "id": source.source_id,
                    "owner": owner_user_id,
                    "version_id": source.version_id,
                    "payload": source.model_dump_json(),
                    "created_at": source.created_at,
                },
            )
            if result.rowcount != 1:
                raise LookupError("Source not found")
            if version is None:
                return
            db.execute(
                text(
                    """
                    INSERT INTO source_versions
                      (version_id, source_id, owner_user_id, object_uri, sha256, record_count,
                       extraction_status, ingested_at, manifest)
                    VALUES (:version_id, :source_id, CAST(:owner AS UUID), :object_uri, :sha256,
                            :record_count, :status, :ingested_at, CAST(:manifest AS JSONB))
                    """
                ),
                {
                    "version_id": version.version_id,
                    "source_id": source.source_id,
                    "owner": owner_user_id,
                    "object_uri": version.object_uri,
                    "sha256": version.sha256,
                    "record_count": version.record_count,
                    "status": version.extraction_status,
                    "ingested_at": version.ingested_at,
                    "manifest": _json(version.manifest),
                },
            )
            if records:
                db.execute(
                    text(
                        """
                        INSERT INTO source_records (version_id, record_index, payload)
                        VALUES (:version_id, :index, CAST(:payload AS JSONB))
                        """
                    ),
                    [
                        {"version_id": version.version_id, "index": index, "payload": _json(record)}
                        for index, record in enumerate(records)
                    ],
                )

    def list_sources(self, owner_user_id: str) -> list[DataSource]:
        """The user's own sources plus shared system sources."""
        with self.engine.connect() as db:
            rows = db.execute(
                text(
                    """
                    SELECT payload FROM data_sources
                    WHERE owner_user_id IN (CAST(:owner AS UUID), CAST(:system AS UUID))
                    ORDER BY created_at DESC
                    """
                ),
                {"owner": owner_user_id, "system": SYSTEM_USER_ID},
            ).all()
        return [DataSource.model_validate(row[0]) for row in rows]

    def get_source(
        self, source_id: str, owner_user_id: str, *, include_shared: bool = True
    ) -> DataSource | None:
        """An owned source, or a shared system source when include_shared (read-only use)."""
        shared = SYSTEM_USER_ID if include_shared else owner_user_id
        with self.engine.connect() as db:
            row = db.execute(
                text(
                    """
                    SELECT payload FROM data_sources
                    WHERE source_id = :id
                      AND owner_user_id IN (CAST(:owner AS UUID), CAST(:system AS UUID))
                    """
                ),
                {"id": source_id, "owner": owner_user_id, "system": shared},
            ).first()
        return DataSource.model_validate(row[0]) if row else None

    def source_preview(
        self,
        source_id: str,
        owner_user_id: str,
        limit: int = 20,
        version_id: str | None = None,
    ) -> list[dict]:
        """Records of one version (default: current), reachable only through an owned source."""
        with self.engine.connect() as db:
            rows = db.execute(
                text(
                    """
                    SELECT r.payload FROM source_records r
                    JOIN source_versions v ON v.version_id = r.version_id
                    JOIN data_sources s ON s.source_id = v.source_id
                    WHERE s.source_id = :id
                      AND s.owner_user_id IN (CAST(:owner AS UUID), CAST(:system AS UUID))
                      AND v.version_id = COALESCE(:version_id, s.current_version_id)
                    ORDER BY r.record_index
                    LIMIT :limit
                    """
                ),
                {
                    "id": source_id,
                    "owner": owner_user_id,
                    "system": SYSTEM_USER_ID,
                    "version_id": version_id,
                    "limit": limit,
                },
            ).all()
        return [row[0] for row in rows]

    def list_source_versions(self, source_id: str, owner_user_id: str) -> list[SourceVersion]:
        with self.engine.connect() as db:
            rows = db.execute(
                text(
                    """
                    SELECT v.version_id, v.source_id, v.object_uri, v.sha256, v.record_count,
                           v.extraction_status, v.ingested_at, v.manifest
                    FROM source_versions v
                    JOIN data_sources s ON s.source_id = v.source_id
                    WHERE s.source_id = :id
                      AND s.owner_user_id IN (CAST(:owner AS UUID), CAST(:system AS UUID))
                    ORDER BY v.ingested_at DESC
                    """
                ),
                {"id": source_id, "owner": owner_user_id, "system": SYSTEM_USER_ID},
            ).all()
        return [
            SourceVersion(
                version_id=row.version_id,
                source_id=row.source_id,
                object_uri=row.object_uri,
                sha256=row.sha256,
                record_count=row.record_count,
                extraction_status=row.extraction_status,
                ingested_at=row.ingested_at,
                manifest=row.manifest,
            )
            for row in rows
        ]

    # Audit -------------------------------------------------------------------

    def audit(
        self,
        event_type: str,
        *,
        owner_user_id: str | None = None,
        investigation_id: str | None = None,
        source_id: str | None = None,
        **detail: Any,
    ) -> None:
        """Record a security-relevant event. Callers must never pass raw tokens."""
        with self.engine.begin() as db:
            db.execute(
                text(
                    """
                    INSERT INTO audit_events
                      (event_type, owner_user_id, investigation_id, source_id, detail)
                    VALUES (:event_type, CAST(:owner AS UUID), :investigation_id, :source_id,
                            CAST(:detail AS JSONB))
                    """
                ),
                {
                    "event_type": event_type,
                    "owner": owner_user_id,
                    "investigation_id": investigation_id,
                    "source_id": source_id,
                    "detail": _json(detail),
                },
            )
