"""Read deterministic demonstration order and billing data."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

# Inside app/ on purpose: the deploy packages the agent directory, and a
# data/ folder at the repo root is NOT shipped. The deployed agent failed with
# FileNotFoundError on /code/data/fixtures.json until this moved.
FIXTURES_PATH = Path(__file__).resolve().parent / "data" / "fixtures.json"

# Fixed so eval cases stay reproducible. Real code would use date.today().
TODAY = date(2026, 9, 12)

_data: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _data
    if _data is None:
        _data = json.loads(FIXTURES_PATH.read_text())
    return _data


def get_order(order_id: str) -> dict[str, Any] | None:
    return _load()["orders"].get(order_id.strip().upper())


def get_invoice(invoice_id: str) -> dict[str, Any] | None:
    return _load()["invoices"].get(invoice_id.strip().upper())


def get_customer(customer_id: str) -> dict[str, Any] | None:
    return _load()["customers"].get(customer_id.strip().upper())


def billing_events(order_id: str) -> list[dict[str, Any]]:
    return _load()["known_billing_events"].get(order_id.strip().upper(), [])


def days_since_delivery(order: dict[str, Any]) -> int | None:
    """Days since delivery, or None if it has not arrived.

    Computed here rather than by the model: date arithmetic is ordinary
    software, and asking an LLM to do it is a design error (BUILD_PLAN 1).
    """
    delivered = order.get("delivered")
    if not delivered:
        return None
    return (TODAY - date.fromisoformat(delivered)).days


def reset() -> None:
    """Clear cached fixture data. Tests only."""
    global _data
    _data = None
