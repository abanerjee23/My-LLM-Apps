"""Agent construction: a root orchestrator over two domain specialists.

Specialists are explicit AgentTools so their result returns to the root before
anything reaches the customer-facing A2A surface. ADK's automatic single-turn
sub-agent wiring worked internally but emitted no final A2A message in live
testing; this explicit boundary keeps the intended three-agent architecture and
makes ownership of the customer voice observable.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.preload_memory_tool import preload_memory_tool
from google.genai import types

from app import callbacks, config, prompts, tools


def _model(model_id: str) -> Gemini:
    # Retries are not optional: a multi-hop turn makes several calls against
    # preview-tier rate limits (BUILD_PLAN 2.10).
    return Gemini(
        model=model_id,
        retry_options=types.HttpRetryOptions(attempts=config.MODEL_RETRY_ATTEMPTS),
    )


def build_returns_specialist() -> LlmAgent:
    return LlmAgent(
        name="returns_exchanges",
        model=_model(config.SPECIALIST_MODEL),
        # The description is what the root routes on, so it states the boundary
        # rather than just the topic (BUILD_PLAN 2.1).
        description=(
            "Decides WHETHER an item can be returned or exchanged: time windows, "
            "item condition, faulty goods, and per-product rules such as final "
            "sale. Owns the returns policy and the product catalogue. Does not "
            "explain how refunds are paid out."
        ),
        instruction=prompts.RETURNS_SPECIALIST,
        tools=tools.RETURNS_TOOLS,
        mode="chat",
    )


def build_billing_specialist() -> LlmAgent:
    return LlmAgent(
        name="billing",
        model=_model(config.SPECIALIST_MODEL),
        description=(
            "Explains HOW money moves: refund timing and routing, pending or "
            "duplicate charges, gift cards, discount codes, delivery charges, "
            "invoices and VAT receipts. Owns the billing policy. Does not decide "
            "whether an item is returnable."
        ),
        instruction=prompts.BILLING_SPECIALIST,
        tools=tools.BILLING_TOOLS,
        mode="chat",
    )


def build_root_agent() -> LlmAgent:
    returns_specialist = build_returns_specialist()
    billing_specialist = build_billing_specialist()
    return LlmAgent(
        name="customer_service",
        model=_model(config.ROOT_MODEL),
        description="Customer service assistant for Tarnfield Running Co.",
        instruction=prompts.ROOT,
        # Preload, not load: this runs on every request and is NOT called by the
        # model, so recall cannot be forgotten. Testing showed the discretionary
        # load_memory tool going unused even on "it's me again" -- the root sees
        # the customer's words, so this is where memory belongs.
        tools=[
            preload_memory_tool,
            AgentTool(agent=returns_specialist),
            AgentTool(agent=billing_specialist),
            *tools.ROOT_TOOLS,
        ],
        # Deterministic, so it does not depend on the model choosing to do it.
        # Specialists run inline in the root's session (2.2), so saving here
        # captures their steps too -- one write per turn, not three.
        after_agent_callback=callbacks.save_conversation_to_memory,
    )
