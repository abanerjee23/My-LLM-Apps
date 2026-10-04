"""One returns/exchanges policy agent; no delegation or action capabilities."""

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.genai import types

from app import callbacks, config, guardrails, prompts


def build_root_agent() -> LlmAgent:
    """Keep the public agent name stable so existing sessions remain readable."""
    return LlmAgent(
        name="customer_service",
        model=Gemini(
            model=config.ROOT_MODEL,
            retry_options=types.HttpRetryOptions(attempts=config.MODEL_RETRY_ATTEMPTS),
        ),
        description="Explains Tarnfield returns and exchanges using current policy evidence.",
        instruction=prompts.POLICY_AGENT,
        output_schema=guardrails.PolicyAnswer,
        before_agent_callback=guardrails.prepare_policy_turn,
        before_model_callback=guardrails.inject_evidence,
        after_model_callback=guardrails.validate_answer,
        after_agent_callback=callbacks.save_conversation_to_memory,
    )
