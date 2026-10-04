"""Verify managed sessions and Memory Bank on the deployed Agent Runtime.

This deliberately creates two different managed sessions for one unique user.
The first stores a synthetic order/item/fault. The second asks a policy-related
recall question. Run only after an approved deployment of the new source.
It spends credits and changes test session/memory records, then removes them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import agentplatform
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

ROOT = Path(__file__).resolve().parent.parent


def _runtime_name() -> str:
    if configured := os.getenv("DEPLOYED_AGENT_RUNTIME_ID"):
        return configured
    metadata = json.loads((ROOT / "deployment_metadata.json").read_text())
    return metadata["remote_agent_runtime_id"]


def _root_text(event: dict[str, Any]) -> str:
    if event.get("author") != "customer_service":
        return ""
    parts = (event.get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts if part.get("text"))


async def _ask(agent: Any, user_id: str, session_id: str, message: str) -> str:
    replies: list[str] = []
    async for event in agent.async_stream_query(
        message=message, user_id=user_id, session_id=session_id
    ):
        if text := _root_text(event):
            replies.append(text)
    return "".join(replies).strip()


async def _wait_for_memory(agent: Any, user_id: str, marker: str) -> float:
    """Measure Memory Bank's eventual-consistency delay before testing recall."""
    started = time.monotonic()
    for _ in range(20):
        result = await agent.async_search_memory(user_id=user_id, query=marker)
        if marker.lower() in str(result).lower():
            return time.monotonic() - started
        await asyncio.sleep(3)
    raise SystemExit("FAIL: Memory Bank did not index the first conversation in 60s.")


async def _cleanup(
    client: Any,
    runtime_name: str,
    location: str,
    user_id: str,
    session_ids: list[str],
) -> None:
    """Remove synthetic records so smoke checks do not pollute analytics."""
    service = VertexAiSessionService(
        project=runtime_name.split("/", 2)[1],
        location=location,
        agent_engine_id=runtime_name.rsplit("/", 1)[-1],
    )
    for session_id in session_ids:
        await service.delete_session(
            app_name=runtime_name,
            user_id=user_id,
            session_id=session_id,
        )
    operation = client.agent_engines.memories.purge(
        name=runtime_name,
        filter=f'scope.user_id="{user_id}"',
        force=True,
    )
    if operation.error:
        raise RuntimeError(f"Memory Bank cleanup failed: {operation.error}")
    if not operation.done:
        raise RuntimeError("Memory Bank cleanup did not reach a completed state.")
    print("Cleanup: removed both synthetic sessions and their Memory Bank entries")


async def main() -> None:
    runtime_name = _runtime_name()
    location = runtime_name.split("/locations/", 1)[1].split("/", 1)[0]
    user_id = f"deployed-memory-smoke-{uuid.uuid4().hex[:10]}"
    client = agentplatform.Client(location=location)
    agent = client.agent_engines.get(name=runtime_name)
    session_ids: list[str] = []

    try:
        first = await agent.async_create_session(user_id=user_id)
        session_ids.append(first["id"])
        first_reply = await _ask(
            agent,
            user_id,
            first["id"],
            "My Scree Trail shoes from order TF-88455 are faulty. Can I exchange them?",
        )

        memory_ready_seconds = await _wait_for_memory(agent, user_id, "TF-88455")

        second = await agent.async_create_session(user_id=user_id)
        session_ids.append(second["id"])
        if first["id"] == second["id"]:
            raise SystemExit(
                "FAIL: Agent Runtime returned the same managed session twice."
            )
        second_reply = await _ask(
            agent,
            user_id,
            second["id"],
            "For my returns and exchanges question, which order and issue do you remember?",
        )

        print(f"Runtime: {runtime_name}")
        print(f"User: {user_id}")
        print(f"Session A: {first['id']}")
        print(f"Session B: {second['id']}")
        print(f"Memory Bank indexing delay: {memory_ready_seconds:.1f}s")
        print(f"Conversation A reply: {first_reply}")
        print(f"Conversation B reply: {second_reply}")

        checks = {
            "different managed sessions": first["id"] != second["id"],
            "sample order": "tf-88455" in second_reply.lower(),
            "reported fault": "faulty" in second_reply.lower(),
        }
        for label, passed in checks.items():
            print(f"{'PASS' if passed else 'FAIL'}: {label}")
        if not all(checks.values()):
            raise SystemExit("FAIL: deployed cross-session recall was incomplete.")
    finally:
        await _cleanup(client, runtime_name, location, user_id, session_ids)


if __name__ == "__main__":
    asyncio.run(main())
