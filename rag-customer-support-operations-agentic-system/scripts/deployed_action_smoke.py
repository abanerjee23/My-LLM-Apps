"""Prove the deployed agent-to-human support-action workflow end to end.

The smoke creates a request through Agent Runtime, verifies its Firestore record,
makes authenticated reviewer decisions through the private dashboard API, asks
the agent for the final status, and removes all synthetic data.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import agentplatform
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService
from google.cloud import firestore

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_URL = os.getenv(
    "SUPPORT_OPS_DASHBOARD_URL",
    "https://support-ops-dashboard-823305428259.us-central1.run.app",
)


def _runtime_name() -> str:
    if configured := os.getenv("DEPLOYED_AGENT_RUNTIME_ID"):
        return configured
    metadata = json.loads((ROOT / "deployment_metadata.json").read_text())
    return metadata["remote_agent_runtime_id"]


def _visible_text(event: dict[str, Any]) -> str:
    parts = (event.get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts if part.get("text"))


async def _ask(agent: Any, user_id: str, session_id: str, message: str) -> str:
    replies: list[str] = []
    async for event in agent.async_stream_query(
        message=message, user_id=user_id, session_id=session_id
    ):
        if text := _visible_text(event):
            replies.append(text)
    return "".join(replies).strip()


def _identity_token() -> str:
    clean_env = dict(os.environ)
    clean_env.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    clean_env.pop("VIRTUAL_ENV", None)
    clean_env.pop("PYTHONPATH", None)
    clean_env["CLOUDSDK_PYTHON"] = "/opt/homebrew/bin/python3.10"
    return subprocess.run(
        [
            "/opt/homebrew/share/google-cloud-sdk/bin/gcloud",
            "auth",
            "print-identity-token",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=clean_env,
    ).stdout.strip()


def _dashboard(
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 30,
) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{DASHBOARD_URL}{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


async def _cleanup(
    client: Any,
    runtime_name: str,
    location: str,
    user_id: str,
    session_id: str | None,
    action_ref: Any,
) -> None:
    failures: list[str] = []
    if action_ref is not None:
        try:
            action_ref.delete()
        except Exception as error:
            failures.append(f"action: {error}")
    if session_id:
        try:
            service = VertexAiSessionService(
                project=runtime_name.split("/", 2)[1],
                location=location,
                agent_engine_id=runtime_name.rsplit("/", 1)[-1],
            )
            await service.delete_session(
                app_name=runtime_name, user_id=user_id, session_id=session_id
            )
        except Exception as error:
            failures.append(f"session: {error}")
    try:
        operation = client.agent_engines.memories.purge(
            name=runtime_name,
            filter=f'scope.user_id="{user_id}"',
            force=True,
        )
        if operation.error:
            failures.append(f"memory: {operation.error}")
    except Exception as error:
        failures.append(f"memory: {error}")
    if failures:
        raise RuntimeError("Synthetic cleanup incomplete: " + "; ".join(failures))


async def main() -> None:
    runtime_name = _runtime_name()
    project = runtime_name.split("/", 2)[1]
    firestore_project = os.getenv("FIRESTORE_PROJECT_ID", "gemini-enterprise-learning")
    location = runtime_name.split("/locations/", 1)[1].split("/", 1)[0]
    user_id = f"action-smoke-{uuid.uuid4().hex[:10]}"
    client = agentplatform.Client(project=project, location=location)
    agent = client.agent_engines.get(name=runtime_name)
    database = firestore.Client(project=firestore_project, database="(default)")
    session_id: str | None = None
    action_ref = None

    try:
        session = await agent.async_create_session(user_id=user_id)
        session_id = session["id"]
        filed_reply = await _ask(
            agent,
            user_id,
            session_id,
            "The sole is separating on the trainers from order TF-88455. I want a "
            "replacement in the same model and size, and I confirm you should file "
            "the request for human review now.",
        )
        match = re.search(r"REQ-[A-F0-9]{10}", filed_reply)
        if not match:
            raise RuntimeError(f"Agent did not return a real request reference: {filed_reply}")
        reference = match.group(0)
        action_ref = database.collection("support_actions").document(reference)
        created = action_ref.get()
        if not created.exists or created.to_dict()["status"] != "pending":
            raise RuntimeError("Agent reference did not resolve to a pending Firestore record.")

        token = _identity_token()
        # Warm the scale-to-zero service with a read-only request before sending
        # versioned workflow transitions that should never be retried blindly.
        _dashboard("/ops/api/metrics", token, timeout=90)
        assigned = _dashboard(
            f"/ops/api/actions/{reference}/assign",
            token,
            {"assignee": "me", "note": "E2E reviewer accepted the item.", "expected_version": 1},
        )
        approved = _dashboard(
            f"/ops/api/actions/{reference}/decision",
            token,
            {"decision": "approve", "note": "Fault evidence supports exchange.", "expected_version": assigned["version"]},
        )
        completed = _dashboard(
            f"/ops/api/actions/{reference}/decision",
            token,
            {"decision": "complete", "note": "Replacement workflow completed.", "expected_version": approved["version"]},
        )
        status_reply = await _ask(
            agent,
            user_id,
            session_id,
            f"What is the latest status of support request {reference}?",
        )
        checks = {
            "agent created durable request": created.exists,
            "review API completed workflow": completed["status"] == "completed",
            "audit trail retained": len(completed["audit"]) == 4,
            "agent recalled live status": "completed" in status_reply.lower(),
        }
        print(f"Reference: {reference}")
        print(f"Filed reply: {filed_reply}")
        print(f"Status reply: {status_reply}")
        for label, passed in checks.items():
            print(f"{'PASS' if passed else 'FAIL'}: {label}")
        if not all(checks.values()):
            raise RuntimeError("Deployed support-action workflow did not pass every check.")
    finally:
        await _cleanup(
            client, runtime_name, location, user_id, session_id, action_ref
        )
        print("Cleanup: removed the synthetic action, session and memory scope")


if __name__ == "__main__":
    asyncio.run(main())
