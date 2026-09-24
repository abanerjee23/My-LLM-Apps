.PHONY: install generate dev test lint frontend frontend-build deploy

install:
	uv sync --extra dev
	npm --prefix frontend install

generate:
	uv run sourcelens-reference-data --reset

dev:
	uv run uvicorn sourcelens.main:app --reload --port 8000

frontend:
	npm --prefix frontend run dev

frontend-build:
	npm --prefix frontend run build

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

deploy:
	bash scripts/deploy_cloud_run.sh
