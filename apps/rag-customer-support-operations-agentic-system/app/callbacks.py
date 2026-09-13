"""Agent callbacks.

Callbacks are hooks that fire around an agent's execution. They are not a memory
feature -- they are simply where deterministic work belongs, as opposed to work
you hope the model remembers to do.

Which is exactly why memory writing lives here. Giving the model a
"save_to_memory" tool would mean asking it to remember to remember; a callback
runs whether or not the model thinks of it.
"""

from __future__ import annotations

import logging

from google.adk.agents.context import Context

logger = logging.getLogger(__name__)


async def save_conversation_to_memory(callback_context: Context) -> None:
    """Write the session to memory after the root agent finishes a turn.

    Without this, `load_memory` searches an empty store: memory would be
    readable and never written (BUILD_PLAN 2.8).

    Runs per turn rather than at conversation end, because a chat has no clean
    "ended" signal. Memory Bank consolidates, so re-adding a growing session is
    the intended usage rather than duplication -- it is also the extra LLM call
    2.8 accepted as the cost of having memory at all.

    Never raises. Memory is an enhancement; failing to write it must not fail the
    customer's turn.

    The parameter MUST be named `callback_context`: ADK invokes after-agent
    callbacks by keyword (`base_agent.py:556`), so a differently-named parameter
    raises TypeError inside the agent run and fails the whole turn. The docstring
    example on `Context.add_session_to_memory` shows `ctx`, which does not work.
    """
    try:
        await callback_context.add_session_to_memory()
    except ValueError:
        # No memory service configured -- e.g. `agents-cli playground` with no
        # --memory_service_uri. Expected, not an error.
        logger.debug("No memory service available; skipping memory write.")
    except Exception:
        logger.warning("Could not write session to memory.", exc_info=True)
