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
    sourcelens_simple_model: str = "gpt-5.6-sol"
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
    galileo_api_key: str | None = None
    galileo_project: str | None = "sourcelens"
    galileo_log_stream: str | None = "log-stream-sourcelens"
    # Authentication (AUTH_PLAN.md). While false, requests without a token act as the
    # system/demo owner so the public app keeps working during the transition.
    auth_required: bool = False
    firebase_project_id: str | None = None
    google_oauth_client_id: str | None = None
    sourcelens_allowed_email: str | None = None
    sourcelens_allowed_origins: str = ""
    sourcelens_session_days: int = 5
    sourcelens_secure_cookies: bool = False
    sourcelens_session_cookie: str = "sourcelens_session"
    sourcelens_csrf_cookie: str = "sourcelens_csrf"
    # Application state lives in Cloud SQL PostgreSQL.
    sourcelens_database_url: str | None = None
    sourcelens_cloud_sql_instance: str = "gemini-enterprise-learning:us-central1:sourcelens-db"
    sourcelens_db_name: str = "sourcelens"
    sourcelens_db_user: str = "sourcelens_app"
    sourcelens_db_password: str | None = None
    sourcelens_db_ip_type: str = "public"
    # Per-user rate limits on expensive routes (requests per hour).
    sourcelens_investigations_per_hour: int = 20
    sourcelens_uploads_per_hour: int = 30

    @property
    def firebase_project(self) -> str:
        return self.firebase_project_id or self.google_cloud_project

    @property
    def database_path(self) -> Path:
        return self.sourcelens_data_dir / "sourcelens.db"

    @property
    def galileo_enabled(self) -> bool:
        return bool(self.galileo_api_key and self.galileo_project and self.galileo_log_stream)

    @property
    def allowed_origins(self) -> set[str]:
        return {
            origin.strip().rstrip("/")
            for origin in self.sourcelens_allowed_origins.replace(";", ",").split(",")
            if origin.strip()
        }


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.sourcelens_data_dir.mkdir(parents=True, exist_ok=True)
    return settings
