from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from sourcelens import main
from sourcelens.config import Settings
from sourcelens.investigator import Investigator
from sourcelens.query import SQLiteWarehouse
from sourcelens.reference_data import generate
from sourcelens.retrieval import LocalEvidenceIndex
from sourcelens.sources import ingest_upload, write_manifest
from sourcelens.store import AppStore


@pytest.mark.asyncio
async def test_csv_upload_preserves_raw_file_and_records(tmp_path):
    upload = UploadFile(
        filename="feedback.csv",
        file=BytesIO(b"product,rating,comment\nNova X300,2,audio drops\nNova X200,5,great sound\n"),
    )
    source, records = await ingest_upload(upload, tmp_path / "sources")
    source_dir = tmp_path / "sources" / source.source_id
    write_manifest(source, records, source_dir)

    assert source.record_count == 2
    assert source.metadata["sha256"]
    assert (source_dir / "feedback.csv").exists()
    assert (source_dir / "manifest.json").exists()
    assert records[0]["comment"] == "audio drops"


def test_source_api_upload_and_connection(tmp_path: Path, monkeypatch):
    database = tmp_path / "sourcelens.db"
    generate(database, reset=True)
    settings = Settings(sourcelens_data_dir=tmp_path, sourcelens_live_agent=False)
    store = AppStore(database)
    investigator = Investigator(
        settings=settings,
        store=store,
        warehouse=SQLiteWarehouse(database),
        evidence_index=LocalEvidenceIndex(database),
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "verify_bigquery", lambda *args: {
        "project": "company", "dataset": "support", "location": "US",
        "tables": ["feedback"], "record_count": 1,
        "records": [{"comment": "Helpful service"}],
    })
    with TestClient(main.app) as client:
        monkeypatch.setattr(main, "store", store)
        monkeypatch.setattr(main, "investigator", investigator)
        before = len(client.get("/api/sources").json())
        preview = client.post(
            "/api/sources/files/preview",
            files={"file": ("returns.csv", b"product,reason\nNova X300,quality\n", "text/csv")},
        )
        assert preview.status_code == 200
        assert len(client.get("/api/sources").json()) == before
        response = client.post(
            "/api/sources/files",
            files={"file": ("returns.csv", b"product,reason\nNova X300,quality\n", "text/csv")},
        )
        assert response.status_code == 201
        assert response.json()["source"]["record_count"] == 1

        connection = client.post(
            "/api/sources/connections",
            json={"name": "Support warehouse", "kind": "bigquery", "dataset": "company.support"},
        )
        assert connection.status_code == 201
        assert connection.json()["status"] == "connected"
        assert len(client.get("/api/sources").json()) >= 2


def test_unverified_connection_is_not_saved(monkeypatch):
    from sourcelens.sources import SourceIngestionError

    def denied(*args):
        raise SourceIngestionError("Access denied")

    monkeypatch.setattr(main, "verify_bigquery", denied)
    with TestClient(main.app) as client:
        before = len(client.get("/api/sources").json())
        response = client.post("/api/sources/connections", json={
            "name": "Denied source", "kind": "bigquery", "project": "company", "dataset": "support"
        })
        assert response.status_code == 422
        assert len(client.get("/api/sources").json()) == before
