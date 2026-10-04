"""Central configuration.

Everything here is a deliberate decision recorded in docs/build-plan.md. The section
references in the comments point at the decision that explains *why*, and what
it costs us.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _ignore_stale_adc() -> None:
    """Drop GOOGLE_APPLICATION_CREDENTIALS when it points at a file that is gone.

    A stale pointer makes ADC raise DefaultCredentialsError instead of falling
    back to gcloud credentials, and the traceback names the missing file rather
    than the real problem. It breaks the agent, the tests and the playground
    alike, so the guard lives here -- everything imports config.

    Only ever removes a pointer to a file that does not exist. A valid one is
    left alone.
    """
    stale = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if stale and not Path(stale).exists():
        print(
            f"warning: GOOGLE_APPLICATION_CREDENTIALS points at a missing file\n"
            f"         ({stale})\n"
            f"         Ignoring it and falling back to gcloud ADC.",
            file=sys.stderr,
        )
        del os.environ["GOOGLE_APPLICATION_CREDENTIALS"]


_ignore_stale_adc()

# --- Model ---------------------------------------------------------------
# One Flash response after deterministic retrieval. No routing/specialist calls.
ROOT_MODEL = "gemini-3.8-flash"

# Bounded retries: reliability without an unbounded latency multiplier.
MODEL_RETRY_ATTEMPTS = 2


# --- Google Cloud (BUILD_PLAN 2.9) --------------------------------------------
# Split billing: model calls go to AI Studio via GEMINI_API_KEY, while RAG Engine
# and Agent Engine are separate clients that still need ADC + this project id.
# Both credential paths must be live at the same time.
def _resolve_project() -> str:
    """Project id from the environment, falling back to the credentials.

    GOOGLE_CLOUD_PROJECT is set by our .env locally, but Agent Runtime does NOT
    set it -- the deployed agent gets its project from the metadata server via
    ADC instead. Reading only the env var meant retrieval silently returned
    "policy lookup unavailable" in production while working perfectly locally.
    That is the local/deployed divergence this project keeps running into, so the
    fix resolves the project the same way the Google client libraries do.
    """
    if env := os.getenv("GOOGLE_CLOUD_PROJECT"):
        return env
    try:
        import google.auth

        _, project = google.auth.default()
        return project or ""
    # Absence is handled by callers: an empty project id degrades retrieval to
    # its "corpus unavailable" path rather than crashing a customer turn.
    except Exception:
        return ""


PROJECT_ID = _resolve_project()

# Model-serving location. "global" is valid here and is the fix for model 404s.
MODEL_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "global")

# RAG Engine corpus location. Deliberately NOT GOOGLE_CLOUD_LOCATION: "global" is
# a model-serving value, not a valid corpus region. Keeping these separate also
# keeps the retrieval round-trip in a region we chose on purpose.
RAG_CORPUS_LOCATION = os.getenv("RAG_CORPUS_LOCATION", "us-central1")

# The corpus is ephemeral (BUILD_PLAN 2.6) so its ID changes on every rag-up.
# Nothing may hardcode an ID; we look the corpus up by this stable display name.
RAG_CORPUS_DISPLAY_NAME = os.getenv(
    "RAG_CORPUS_DISPLAY_NAME", "customer-service-policies"
)

# Only these two documents are permitted as policy evidence.
RETURNS_POLICY_DOCS = [
    os.getenv("RETURNS_POLICY_DOC", "tarnfield_returns_policy.pdf"),
    os.getenv("PRODUCT_CATALOGUE_DOC", "tarnfield_product_catalogue.pdf"),
]


# --- Sessions (BUILD_PLAN 2.7) ------------------------------------------------
# SQLite locally, Agent Engine managed sessions once deployed. SQLite is not an
# option in production: Agent Runtime filesystems are ephemeral, so a session DB
# written on one instance may not exist for the next request.
SESSION_BACKEND = os.getenv("SESSION_BACKEND", "sqlite")
SESSION_DB_PATH = os.getenv("SESSION_DB_PATH", "./.sessions/sessions.db")

# Set by Agent Runtime at deploy time, and required by BOTH
# VertexAiSessionService and VertexAiMemoryBankService.
#
# The name is the runtime's, not ours: app/app_utils/services.py reads
# GOOGLE_CLOUD_AGENT_ENGINE_ID directly because that is what Agent Runtime
# injects. Defining a second name here would be a config that looks wired and
# is not, so this constant mirrors the runtime's and never competes with it.
AGENT_ENGINE_ID = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID", "")


# --- Memory --------------------------------------------------------------
# The runtime uses Memory Bank; standalone local runners use in-process memory.
# The chat gateway defaults to managed state even while executing local source.
# Only fresh retrieved policy is authoritative; recalled details are context.
