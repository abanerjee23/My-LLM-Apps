"""Read-only context gathering for the policy assistant."""

from __future__ import annotations

import re
from typing import Any

from app import config, fixtures, retrieval


def get_policy_context(question: str) -> dict[str, Any]:
    """Retrieve current policy and optionally enrich a sample order reference."""
    order_facts = None
    match = re.search(r"\bTF-\d+\b", question, re.IGNORECASE)
    if match:
        reference = match.group().upper()
        order = fixtures.get_order(reference)
        if order:
            order_facts = {
                "sample_data": True,
                "reference": reference,
                "days_since_delivery": fixtures.days_since_delivery(order),
                "items": order["items"],
                "reported_fault": order.get("reported_fault"),
            }
            question += " Products: " + ", ".join(
                item["name"] for item in order["items"]
            )
        else:
            order_facts = {"sample_data": True, "reference": reference, "found": False}
    evidence = retrieval.retrieve(question, config.RETURNS_POLICY_DOCS)
    return {**evidence, "order": order_facts}
