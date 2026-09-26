"""Import existing portfolio records from the local SQLite app state into Cloud SQL.

Every record moves to the dedicated system/demo owner (AUTH_PLAN.md, migration step 5).
Notebook snapshots and source manifests are copied unchanged. Safe to re-run.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from sqlalchemy import text

from sourcelens.config import get_settings
from sourcelens.db import create_database_engine
from sourcelens.store import SYSTEM_USER_ID


def _rows(db: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return db.execute(f"SELECT * FROM {table}").fetchall() if exists else []


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, default=settings.database_path)
    args = parser.parse_args()

    source_db = sqlite3.connect(args.sqlite)
    source_db.row_factory = sqlite3.Row
    engine = create_database_engine(settings)
    counts: dict[str, int] = {}
    owner = {"owner": SYSTEM_USER_ID}

    with engine.begin() as db:
        for row in _rows(source_db, "data_sources"):
            records = [
                json.loads(record["payload"])
                for record in source_db.execute(
                    "SELECT payload FROM source_records WHERE source_id = ? ORDER BY record_index",
                    (row["source_id"],),
                )
            ]
            payload = json.loads(row["payload"])
            version_id = f"v-portfolio-{row['source_id']}" if records else None
            payload["version_id"] = version_id
            inserted = db.execute(
                text(
                    """
                    INSERT INTO data_sources (source_id, owner_user_id, current_version_id, payload, created_at)
                    VALUES (:id, CAST(:owner AS UUID), :version_id, CAST(:payload AS JSONB), :created_at)
                    ON CONFLICT (source_id) DO NOTHING
                    """
                ),
                {
                    "id": row["source_id"],
                    "version_id": version_id,
                    "payload": json.dumps(payload),
                    "created_at": row["created_at"],
                    **owner,
                },
            ).rowcount
            counts["data_sources"] = counts.get("data_sources", 0) + inserted
            if inserted and records:
                db.execute(
                    text(
                        """
                        INSERT INTO source_versions
                          (version_id, source_id, owner_user_id, object_uri, sha256, record_count,
                           extraction_status, ingested_at, manifest)
                        VALUES (:version_id, :id, CAST(:owner AS UUID), :uri, :sha256, :count,
                                'extracted', :created_at, CAST(:manifest AS JSONB))
                        """
                    ),
                    {
                        "version_id": version_id,
                        "id": row["source_id"],
                        "uri": payload.get("metadata", {}).get("archive_uri"),
                        "sha256": payload.get("metadata", {}).get("sha256"),
                        "count": len(records),
                        "created_at": row["created_at"],
                        "manifest": json.dumps({"source": payload, "imported_from": "portfolio"}),
                        **owner,
                    },
                )
                db.execute(
                    text(
                        "INSERT INTO source_records (version_id, record_index, payload) "
                        "VALUES (:version_id, :index, CAST(:payload AS JSONB))"
                    ),
                    [
                        {"version_id": version_id, "index": index, "payload": json.dumps(record)}
                        for index, record in enumerate(records)
                    ],
                )

        for row in _rows(source_db, "investigations"):
            counts["investigations"] = counts.get("investigations", 0) + db.execute(
                text(
                    """
                    INSERT INTO investigations (investigation_id, owner_user_id, payload, updated_at)
                    VALUES (:id, CAST(:owner AS UUID), CAST(:payload AS JSONB), :updated_at)
                    ON CONFLICT (investigation_id) DO NOTHING
                    """
                ),
                {"id": row["investigation_id"], "payload": row["payload"], "updated_at": row["updated_at"], **owner},
            ).rowcount

        for row in _rows(source_db, "notebook_entries"):
            payload = json.loads(row["payload"])
            payload["owner_user_id"] = SYSTEM_USER_ID
            counts["notebook_entries"] = counts.get("notebook_entries", 0) + db.execute(
                text(
                    """
                    INSERT INTO notebook_entries
                      (entry_id, investigation_id, owner_user_id, revision, saved_at, payload)
                    VALUES (:id, :investigation_id, CAST(:owner AS UUID), :revision, :saved_at,
                            CAST(:payload AS JSONB))
                    ON CONFLICT (entry_id) DO NOTHING
                    """
                ),
                {
                    "id": row["entry_id"],
                    "investigation_id": row["investigation_id"],
                    "revision": row["revision"],
                    "saved_at": row["saved_at"],
                    "payload": json.dumps(payload),
                    **owner,
                },
            ).rowcount

        already = db.execute(text("SELECT COUNT(*) FROM usage_ledger WHERE owner_user_id = CAST(:owner AS UUID)"), owner).scalar_one()
        if not already:
            for row in _rows(source_db, "usage_ledger"):
                db.execute(
                    text(
                        """
                        INSERT INTO usage_ledger (investigation_id, owner_user_id, model, input_tokens,
                          output_tokens, estimated_cost, created_at)
                        VALUES (:id, CAST(:owner AS UUID), :model, :input, :output, :cost, :created_at)
                        """
                    ),
                    {
                        "id": row["investigation_id"],
                        "model": row["model"],
                        "input": row["input_tokens"],
                        "output": row["output_tokens"],
                        "cost": row["estimated_cost"],
                        "created_at": row["created_at"],
                        **owner,
                    },
                )
                counts["usage_ledger"] = counts.get("usage_ledger", 0) + 1

    print(json.dumps({"imported_to_system_owner": counts}, indent=2))


if __name__ == "__main__":
    main()
