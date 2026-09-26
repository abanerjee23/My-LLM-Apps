# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Process-wide ADK session/artifact services shared by every serving surface.

Registered under ``shared://`` so the ADK web routes, the A2A path, and the
reasoning_engine adapter share one instance: a session created on any surface
is visible to the others.
"""

from __future__ import annotations

import functools
import os

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.service_registry import get_service_registry
from google.adk.cli.utils.service_factory import create_session_service_from_options

from app import config

SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"

_AGENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@functools.cache
def get_session_service():
    """Process-wide session service shared across every serving surface."""
    if uri := os.environ.get("SESSION_SERVICE_URI"):
        return create_session_service_from_options(
            base_dir=_AGENT_DIR, session_service_uri=uri
        )
    if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

        # config.PROJECT_ID, not the env var: Agent Runtime strips
        # GOOGLE_CLOUD_PROJECT from the deployed environment, so reading it
        # directly yields None here. Same trap that broke retrieval on deploy.
        return VertexAiSessionService(
            project=config.PROJECT_ID,
            # Runtime-injected agent-engine region, not GOOGLE_CLOUD_LOCATION
            # (which agent.py pins to "global").
            location=os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION"),
            agent_engine_id=agent_engine_id,
        )
    # Local: SQLite rather than in-memory, so a conversation survives a restart
    # and is inspectable with any SQL client while debugging three agents
    # (BUILD_PLAN 2.7). SQLite is never used deployed -- Agent Runtime
    # filesystems are ephemeral, so sessions would vanish non-deterministically.
    if os.environ.get("SESSION_BACKEND", "sqlite") == "sqlite":
        from google.adk.sessions.sqlite_session_service import SqliteSessionService

        db_path = os.environ.get("SESSION_DB_PATH", "./.sessions/sessions.db")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        return SqliteSessionService(db_path=db_path)

    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    return InMemorySessionService()


@functools.cache
def get_memory_service():
    """Process-wide memory service (BUILD_PLAN 2.8).

    Separate service from sessions, and a separate job: sessions hold the literal
    event log of one conversation, memory holds distilled facts about a customer
    across conversations.

    Memory Bank is bound to a DEPLOYED Agent Engine, so locally this is a
    non-persistent stand-in that exercises the code path but forgets on restart.
    Memory-dependent behaviour is therefore not trustworthy until deployed --
    that divergence is a known cost, not a surprise.
    """
    if agent_engine_id := os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
        from google.adk.memory.vertex_ai_memory_bank_service import (
            VertexAiMemoryBankService,
        )

        return VertexAiMemoryBankService(
            project=config.PROJECT_ID,  # see get_session_service
            location=os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION"),
            agent_engine_id=agent_engine_id,
        )

    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService

    return InMemoryMemoryService()


@functools.cache
def get_artifact_service():
    """Process-wide artifact service: GCS when a bucket is set, else in-memory."""
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


_registry = get_service_registry()
_registry.register_session_service("shared", lambda uri, **kw: get_session_service())
_registry.register_artifact_service("shared", lambda uri, **kw: get_artifact_service())
