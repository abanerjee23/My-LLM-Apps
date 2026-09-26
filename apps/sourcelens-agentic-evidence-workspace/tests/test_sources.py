from io import BytesIO

import pytest
from fastapi import UploadFile

from sourcelens.sources import ingest_upload, write_manifest


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


def test_preview_does_not_save_and_upload_does(client, make_user, tokens):
    headers = tokens.headers(make_user())
    before = len(client.get("/api/sources", headers=headers).json())
    file = {"file": ("returns.csv", b"product,reason\nNova X300,quality\n", "text/csv")}
    preview = client.post("/api/sources/files/preview", files=file, headers=headers)
    assert preview.status_code == 200
    assert "raw_path" not in preview.json()["source"]["metadata"]
    assert len(client.get("/api/sources", headers=headers).json()) == before

    response = client.post("/api/sources/files", files=file, headers=headers)
    assert response.status_code == 201
    source = response.json()["source"]
    assert source["record_count"] == 1
    assert "raw_path" not in source["metadata"]
    assert len(client.get("/api/sources", headers=headers).json()) == before + 1


def test_unsupported_file_is_rejected_and_not_saved(client, make_user, tokens):
    headers = tokens.headers(make_user())
    before = len(client.get("/api/sources", headers=headers).json())
    response = client.post(
        "/api/sources/files", files={"file": ("notes.txt", b"hello", "text/plain")}, headers=headers
    )
    assert response.status_code == 422
    assert len(client.get("/api/sources", headers=headers).json()) == before
