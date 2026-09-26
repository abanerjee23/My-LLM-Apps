.PHONY: install generate dev test test-live lint frontend frontend-build deploy db-start db-stop db-status db-migrate db-import-demo

GCP_PROJECT ?= gemini-enterprise-learning
SQL_INSTANCE ?= sourcelens-db
OWNER_EMAIL ?= abanerje.08@gmail.com
# Fetched from Secret Manager per command; never written to disk.
DB_PASSWORD = SOURCELENS_DB_PASSWORD="$$(gcloud secrets versions access latest --secret=sourcelens-db-password --project=$(GCP_PROJECT))"
# Tests run against the real services: Cloud SQL, the BigQuery warehouse and the raw bucket.
REAL_SERVICES = SOURCELENS_WAREHOUSE=bigquery GOOGLE_CLOUD_PROJECT=$(GCP_PROJECT) SOURCELENS_GCS_BUCKET=$(GCP_PROJECT)-sourcelens-raw

install:
	uv sync --extra dev
	npm --prefix frontend install

generate:
	uv run sourcelens-reference-data --reset

dev:
	$(DB_PASSWORD) AUTH_REQUIRED=true SOURCELENS_ALLOWED_EMAIL=$(OWNER_EMAIL) uv run python -m uvicorn sourcelens.main:app --reload --port 8000

frontend:
	npm --prefix frontend run dev

frontend-build:
	npm --prefix frontend run build

test:
	$(DB_PASSWORD) $(REAL_SERVICES) uv run pytest -q

# Adds real model calls (needs OPENAI_API_KEY); costs money per run.
test-live:
	$(DB_PASSWORD) $(REAL_SERVICES) SOURCELENS_TEST_LIVE_MODEL=1 uv run pytest -q -m live_model

lint:
	uv run ruff check src tests

deploy:
	bash scripts/deploy_cloud_run.sh

# Cloud SQL bills per hour while running. Stopping keeps all data. See GOOGLE_SERV.md.
db-start:
	gcloud sql instances patch $(SQL_INSTANCE) --activation-policy=ALWAYS --project=$(GCP_PROJECT) --quiet

db-stop:
	gcloud sql instances patch $(SQL_INSTANCE) --activation-policy=NEVER --project=$(GCP_PROJECT) --quiet

db-migrate:
	$(DB_PASSWORD) uv run alembic upgrade head

db-import-demo:
	$(DB_PASSWORD) uv run python scripts/import_demo_data.py

db-status:
	gcloud sql instances describe $(SQL_INSTANCE) --project=$(GCP_PROJECT) --format='value(state,settings.activationPolicy)'
