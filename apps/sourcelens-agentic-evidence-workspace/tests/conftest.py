from __future__ import annotations

from pathlib import Path

import pytest

from sourcelens.config import Settings
from sourcelens.investigator import Investigator
from sourcelens.query import SQLiteWarehouse
from sourcelens.reference_data import generate
from sourcelens.retrieval import LocalEvidenceIndex
from sourcelens.store import AppStore


@pytest.fixture
def services(tmp_path: Path):
    path = tmp_path / "sourcelens.db"
    generate(path, reset=True)
    settings = Settings(sourcelens_data_dir=tmp_path, sourcelens_live_agent=False)
    store = AppStore(path)
    investigator = Investigator(
        settings=settings,
        store=store,
        warehouse=SQLiteWarehouse(path),
        evidence_index=LocalEvidenceIndex(path),
    )
    return store, investigator
