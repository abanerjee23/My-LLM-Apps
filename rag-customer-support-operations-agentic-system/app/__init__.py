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

"""Application bootstrap.

`.env` is deployment-facing, non-secret configuration. `.env.local` is a
git-ignored local override for developer secrets. Load both before importing
the agent: model and RAG configuration is resolved during that import.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)

# Agent Runtime receives secrets from Secret Manager. A local override must
# never replace runtime-injected configuration in production.
if not os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
    load_dotenv(_PROJECT_ROOT / ".env.local", override=True)

__all__ = ["app"]


def __getattr__(name: str):
    """Load the expensive agent graph only when the ADK entry point asks for it.

    Gateway and utility imports need not instantiate the Gemini client or RAG
    dependencies until an agent-serving entry point actually needs them.
    """
    if name == "app":
        from .agent import app as agent_app

        return agent_app
    raise AttributeError(name)
