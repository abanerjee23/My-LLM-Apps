from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .models import DataSource, Investigation, NotebookEntry, ReviewRequest, utc_now


class AppStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS investigations (
                    investigation_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notebook_entries (
                    entry_id TEXT PRIMARY KEY,
                    investigation_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    saved_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    investigation_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    estimated_cost REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_sources (
                    source_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_records (
                    source_id TEXT NOT NULL,
                    record_index INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(source_id, record_index)
                );
                """
            )

    def save_investigation(self, investigation: Investigation) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                """INSERT INTO investigations VALUES (?, ?, ?)
                ON CONFLICT(investigation_id) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (
                    investigation.investigation_id,
                    investigation.model_dump_json(),
                    investigation.updated_at.isoformat(),
                ),
            )

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM investigations WHERE investigation_id = ?",
                (investigation_id,),
            ).fetchone()
        return Investigation.model_validate_json(row["payload"]) if row else None

    def list_investigations(self) -> list[Investigation]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM investigations ORDER BY updated_at DESC LIMIT 100").fetchall()
        return [Investigation.model_validate_json(row["payload"]) for row in rows]

    def count_live_runs(self) -> int:
        with self.connect() as db:
            return int(
                db.execute("SELECT COUNT(DISTINCT investigation_id) FROM usage_ledger").fetchone()[0]
            )

    def save_review(self, investigation: Investigation, review: ReviewRequest) -> NotebookEntry:
        with self._lock, self.connect() as db:
            revision = int(
                db.execute(
                    "SELECT COALESCE(MAX(revision), 0) + 1 FROM notebook_entries WHERE investigation_id = ?",
                    (investigation.investigation_id,),
                ).fetchone()[0]
            )
            entry = NotebookEntry(
                entry_id=str(uuid4()),
                investigation_id=investigation.investigation_id,
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
                "INSERT INTO notebook_entries VALUES (?, ?, ?, ?, ?)",
                (
                    entry.entry_id,
                    entry.investigation_id,
                    entry.revision,
                    entry.saved_at.isoformat(),
                    entry.model_dump_json(),
                ),
            )
        return entry

    def list_notebook(self) -> list[NotebookEntry]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM notebook_entries ORDER BY saved_at DESC"
            ).fetchall()
        return [NotebookEntry.model_validate_json(row["payload"]) for row in rows]

    def add_usage(
        self,
        investigation_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
    ) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO usage_ledger(investigation_id, model, input_tokens, output_tokens, estimated_cost, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    investigation_id,
                    model,
                    input_tokens,
                    output_tokens,
                    estimated_cost,
                    utc_now().isoformat(),
                ),
            )

    def usage_cost(self) -> float:
        with self.connect() as db:
            return float(db.execute("SELECT COALESCE(SUM(estimated_cost), 0) FROM usage_ledger").fetchone()[0])

    def export_notebook(self) -> str:
        return json.dumps([entry.model_dump(mode="json") for entry in self.list_notebook()], indent=2)

    def save_source(self, source: DataSource, records: list[dict] | None = None) -> None:
        with self._lock, self.connect() as db:
            db.execute(
                """INSERT INTO data_sources VALUES (?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET payload=excluded.payload""",
                (source.source_id, source.model_dump_json(), source.created_at.isoformat()),
            )
            if records is not None:
                db.execute("DELETE FROM source_records WHERE source_id = ?", (source.source_id,))
                db.executemany(
                    "INSERT INTO source_records VALUES (?, ?, ?)",
                    [
                        (source.source_id, index, json.dumps(record, default=str))
                        for index, record in enumerate(records)
                    ],
                )

    def list_sources(self) -> list[DataSource]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM data_sources ORDER BY created_at DESC").fetchall()
        return [DataSource.model_validate_json(row["payload"]) for row in rows]

    def get_source(self, source_id: str) -> DataSource | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload FROM data_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return DataSource.model_validate_json(row["payload"]) if row else None

    def source_preview(self, source_id: str, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM source_records WHERE source_id = ? ORDER BY record_index LIMIT ?",
                (source_id, limit),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]
