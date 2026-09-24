from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(PROJECT_DIR.parent / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    sourcelens_data_dir: Path = PROJECT_DIR / "data"
    sourcelens_warehouse: str = "sqlite"
    sourcelens_complex_model: str = "gpt-5.6-sol"
    sourcelens_simple_model: str = "gpt-5.6-luna"
    sourcelens_reasoning_effort: str = "medium"
    sourcelens_live_agent: bool = False
    sourcelens_max_live_runs: int = 15
    sourcelens_model_budget_usd: float = 9.0
    google_cloud_project: str = "gemini-enterprise-learning"
    google_cloud_location: str = "us-central1"
    sourcelens_bigquery_dataset: str = "sourcelens_demo"
    sourcelens_gcs_bucket: str | None = None
    openai_api_key: str | None = None
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "sourcelens-feedback-v1"

    @property
    def database_path(self) -> Path:
        return self.sourcelens_data_dir / "sourcelens.db"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.sourcelens_data_dir.mkdir(parents=True, exist_ok=True)
    return settings

