from pathlib import Path

from fastapi.testclient import TestClient

from sourcelens import main
from sourcelens.config import Settings
from sourcelens.investigator import Investigator
from sourcelens.query import SQLiteWarehouse
from sourcelens.reference_data import generate
from sourcelens.retrieval import LocalEvidenceIndex
from sourcelens.store import AppStore


def test_complete_api_flow(tmp_path: Path, monkeypatch):
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
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "investigator", investigator)

    with TestClient(main.app) as client:
        # lifespan rebuilds globals, so replace them after entry.
        monkeypatch.setattr(main, "store", store)
        monkeypatch.setattr(main, "investigator", investigator)
        response = client.post(
            "/api/investigations",
            json={"brief": "Why did Nova X300 decline in the latest quarter?"},
        )
        assert response.status_code == 202
        investigation_id = response.json()["investigation_id"]
        result = client.get(f"/api/investigations/{investigation_id}").json()
        assert result["status"] == "ready"
        refined = client.post(
            f"/api/investigations/{investigation_id}/refine",
            json={"direction": "Test stock availability before concluding"},
        )
        assert refined.status_code == 202
        result = client.get(f"/api/investigations/{investigation_id}").json()
        assert result["status"] == "ready"
        assert any(event["event_type"] == "direction" for event in result["events"])
        review = client.post(
            f"/api/investigations/{investigation_id}/review",
            json={"decision": "accepted", "accuracy_rating": 4, "usefulness_rating": 5},
        )
        assert review.status_code == 200
        assert client.get("/api/notebook").json()[0]["decision"] == "accepted"
