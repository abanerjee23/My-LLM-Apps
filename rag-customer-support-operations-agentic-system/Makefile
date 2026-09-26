# customer-chatbot-rag
#
# The corpus commands are the ones that matter. Vertex AI RAG Engine bills
# continuously, and the billing tier is a project+location setting rather than a
# property of your corpus -- so `rag-down` alone does NOT stop the meter.
# See BUILD_PLAN.md 2.5 and 2.6.

PORT ?= 8080
SESSION_DB ?= ./.sessions/sessions.db

.DEFAULT_GOAL := help
.PHONY: help rag-up rag-status rag-down rag-serverless rag-unprovision playground playground-plain smoke memory-smoke deployed-memory-smoke deployed-action-smoke dashboard test eval lint docs

help:  ## Show this help
	@echo "customer-chatbot-rag"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Corpus billing is a standing cost. Run 'make rag-status' before you walk away."

# --- Corpus lifecycle (BUILD_PLAN 2.6) ---------------------------------------

rag-up:  ## Create the corpus and ingest docs/*.pdf
	uv run python scripts/rag_corpus.py up

rag-status:  ## Show corpus contents AND the billing tier
	uv run python scripts/rag_corpus.py status

rag-down:  ## Delete the corpus (does NOT stop the bill -- see rag-unprovision)
	uv run python scripts/rag_corpus.py down

rag-serverless:  ## Switch to serverless mode -- billed per use, no standing instance
	uv run python scripts/rag_corpus.py serverless

rag-unprovision:  ## Halt RAG Engine billing. Destructive, project-wide.
	uv run python scripts/rag_corpus.py unprovision

# --- Development --------------------------------------------------------------

playground:  ## Interactive local testing, with the same services the server uses
	@# `agents-cli playground` shells out to `adk web .` and passes no service
	@# flags, so it would run on ADK's in-memory defaults -- conversations lost on
	@# restart, and no memory service at all. We pass them explicitly so the
	@# playground exercises what BUILD_PLAN 2.7/2.8 actually specify.
	@mkdir -p .sessions
	uv run adk web . --host 127.0.0.1 --port $(PORT) --allow_origins '*' --reload_agents \
		--session_service_uri "sqlite://$(SESSION_DB)" \
		--memory_service_uri "memory://"
	@echo "Open http://127.0.0.1:$(PORT)/dev-ui/?app=app"

playground-plain:  ## Playground via agents-cli (ADK defaults; no persistence)
	agents-cli playground

test:  ## Unit and integration tests
	uv run pytest tests/unit tests/integration

smoke:  ## Run real conversations through the agent (spends credits; corpus must be up)
	@SMOKE_Q="$(Q)" uv run python scripts/smoke.py

memory-smoke:  ## Prove recall across two conversations (spends credits)
	@uv run python scripts/memory_smoke.py

deployed-memory-smoke:  ## Prove Agent Runtime recall across managed sessions
	@uv run python scripts/deployed_memory_smoke.py

deployed-action-smoke:  ## Prove agent → Firestore → private reviewer → status workflow
	@uv run python scripts/deployed_action_smoke.py

dashboard:  ## Run the Support Operations dashboard locally
	@uv run uvicorn app.dashboard_app:app --host 127.0.0.1 --port $(PORT)

eval:  ## Run the eval dataset (corpus must be up -- BUILD_PLAN 6.5)
	@uv run python scripts/rag_corpus.py status | grep -q "^Corpus   : projects/" \
		|| { echo "No corpus. Run 'make rag-up' first, or evals grade a broken system."; exit 1; }
	agents-cli eval run

lint:  ## Code quality checks
	agents-cli lint

docs:  ## Regenerate the billing policy PDF from source
	uv run --with reportlab python scripts/make_billing_policy.py
