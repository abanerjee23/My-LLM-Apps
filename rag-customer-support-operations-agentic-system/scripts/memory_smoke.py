"""Prove that customer context is recalled across separate conversations.

This is a behavioural smoke check, not a deterministic unit test. It makes live
model calls and therefore spends credits::

    make memory-smoke

The first conversation supplies a sample order and reported fault. The second
starts with a new session for the same user and asks a policy-related recall
question. Only selected details enter memory, not contact data or conclusions.
Local memory is in-process; managed Memory Bank needs a separate live check.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.adk.runners import Runner

from app.agent import app as adk_app
from app.app_utils import services
from scripts.smoke import ask


def _show(label: str, reply: str, elapsed: float, timeline: list[str]) -> None:
    print(f"\n{label}")
    for step in timeline:
        print(f"   · {step}")
    print(f"\nAGENT: {reply or '(no text returned)'}")
    print(f"\n[completed={elapsed:.1f}s]")


async def main() -> None:
    run_id = uuid.uuid4().hex[:10]
    user_id = f"memory-smoke-{run_id}"
    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        memory_service=services.get_memory_service(),
        auto_create_session=True,
    )

    print("Cross-conversation recall check")
    print(f"Customer identity: {user_id}")

    first_message = (
        "My Scree Trail shoes from order TF-88455 are faulty. Can I exchange them?"
    )
    print(f"\nCONVERSATION A — CUSTOMER: {first_message}")
    started = time.monotonic()
    first_reply, first_metrics = await ask(
        runner, user_id, f"memory-a-{run_id}", first_message
    )
    _show(
        "CONVERSATION A — RESULT",
        first_reply,
        time.monotonic() - started,
        first_metrics.timeline,
    )

    second_message = (
        "For my returns and exchanges question, which order and issue do you remember?"
    )
    print(f"\nCONVERSATION B — CUSTOMER: {second_message}")
    started = time.monotonic()
    second_reply, second_metrics = await ask(
        runner, user_id, f"memory-b-{run_id}", second_message
    )
    _show(
        "CONVERSATION B — RESULT",
        second_reply,
        time.monotonic() - started,
        second_metrics.timeline,
    )

    expected = {
        "sample order": "tf-88455" in second_reply.lower(),
        "reported fault": "faulty" in second_reply.lower(),
    }
    print("\nCHECKS")
    for name, passed in expected.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")

    if not all(expected.values()):
        raise SystemExit(
            "Cross-conversation recall was incomplete. Inspect the two replies above."
        )


if __name__ == "__main__":
    asyncio.run(main())
