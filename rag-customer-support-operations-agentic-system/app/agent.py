# ruff: noqa
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

"""Entry point. The architecture lives in app/agents.py; see BUILD_PLAN.md 3."""

from google.adk.apps import App

from app.agents import build_root_agent
from app.app_utils.observability import setup_arize_observability

# CLI and SDK entry points also need automatic instrumentation. The HTTP server
# initializes its Google Cloud provider first; setup reuses that provider.
setup_arize_observability()

root_agent = build_root_agent()

app = App(
    root_agent=root_agent,
    name="app",
)
