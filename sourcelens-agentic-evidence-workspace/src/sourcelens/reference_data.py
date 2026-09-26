from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import get_settings

PRODUCTS = [
    ("NOVA-X100", "Nova X100", "headphones", 99.0),
    ("NOVA-X200", "Nova X200", "headphones", 149.0),
    ("NOVA-X300", "Nova X300", "headphones", 199.0),
    ("NOVA-PRO", "Nova Pro", "headphones", 299.0),
]


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _add_months(day: date, months: int) -> date:
    absolute = day.year * 12 + day.month - 1 + months
    return date(absolute // 12, absolute % 12 + 1, 1)


def generate(path: Path, *, reset: bool = False, dataset_version: int = 42) -> dict[str, int]:
    """Create a coherent local business dataset with a held-out X300 quality scenario."""
    if reset and path.exists():
        path.unlink()
    rng = random.Random(dataset_version)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(
        """
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS sales_monthly;
        DROP TABLE IF EXISTS sales_lines;
        DROP TABLE IF EXISTS inventory_monthly;
        DROP TABLE IF EXISTS returns_monthly;
        DROP TABLE IF EXISTS feedback;
        CREATE TABLE products (
            product_id TEXT PRIMARY KEY, product_name TEXT, category TEXT,
            launch_date TEXT, list_price REAL
        );
        CREATE TABLE sales_monthly (
            month TEXT, product_id TEXT, channel TEXT, units INTEGER,
            gross_revenue REAL, discount_amount REAL, refund_amount REAL,
            net_revenue REAL, realized_price REAL
        );
        CREATE TABLE sales_lines (
            sales_line_id TEXT PRIMARY KEY, sale_date TEXT, product_id TEXT,
            channel TEXT, units INTEGER, gross_revenue REAL,
            discount_amount REAL, refund_amount REAL, net_revenue REAL
        );
        CREATE TABLE inventory_monthly (
            month TEXT, product_id TEXT, stockout_days INTEGER, availability_rate REAL
        );
        CREATE TABLE returns_monthly (
            month TEXT, product_id TEXT, returned_units INTEGER, quality_returns INTEGER,
            return_rate REAL
        );
        CREATE TABLE feedback (
            feedback_id TEXT PRIMARY KEY, product_id TEXT, feedback_date TEXT,
            rating INTEGER, sentiment REAL, theme TEXT, title TEXT, body TEXT,
            source TEXT, verified_purchase INTEGER, raw_hash TEXT
        );
        """
    )
    db.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?)",
        [(pid, name, category, "2024-01-01", price) for pid, name, category, price in PRODUCTS],
    )

    start = date(2024, 1, 1)
    feedback_count = 0
    sales_count = 0
    sales_line_count = 0
    for month_index in range(24):
        month = _add_months(start, month_index)
        month_s = month.isoformat()
        seasonal = 1.32 if month.month in {11, 12} else 1.0
        for product_index, (product_id, product_name, _, list_price) in enumerate(PRODUCTS):
            quality_incident = product_id == "NOVA-X300" and month >= date(2025, 10, 1)
            trend = 1 + month_index * 0.018
            demand = (850 + product_index * 180) * trend * seasonal
            if quality_incident:
                demand *= 0.67
            stockout_days = 1 + rng.randint(0, 2)
            if product_id == "NOVA-PRO" and month.month in {11, 12}:
                stockout_days += 5
            availability = max(0.65, 1 - stockout_days / 31)
            units_total = int(demand * availability * rng.uniform(0.96, 1.04))
            discount_rate = 0.08 if month.month in {6, 11} else 0.025
            if product_id == "NOVA-X300" and month >= date(2025, 7, 1):
                list_price = 219.0
            returned_units = int(units_total * (0.045 if quality_incident else 0.018))
            quality_returns = int(returned_units * (0.78 if quality_incident else 0.3))
            for channel, share in (("direct", 0.55), ("marketplace", 0.45)):
                units = int(units_total * share)
                gross = units * list_price
                discount = gross * discount_rate
                refund = returned_units * share * list_price
                net = gross - discount - refund
                db.execute(
                    "INSERT INTO sales_monthly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (month_s, product_id, channel, units, gross, discount, refund, net, net / max(units, 1)),
                )
                sales_count += 1
                gross_per_unit = list_price
                discount_per_unit = list_price * discount_rate
                refund_per_unit = refund / max(units, 1)
                lines = []
                for _ in range(units):
                    sales_line_count += 1
                    sale_day = month + timedelta(days=rng.randint(0, 27))
                    lines.append(
                        (
                            f"SL-{sales_line_count:08d}",
                            sale_day.isoformat(),
                            product_id,
                            channel,
                            1,
                            gross_per_unit,
                            discount_per_unit,
                            refund_per_unit,
                            gross_per_unit - discount_per_unit - refund_per_unit,
                        )
                    )
                db.executemany("INSERT INTO sales_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", lines)
            db.execute(
                "INSERT INTO inventory_monthly VALUES (?, ?, ?, ?)",
                (month_s, product_id, stockout_days, availability),
            )
            db.execute(
                "INSERT INTO returns_monthly VALUES (?, ?, ?, ?, ?)",
                (month_s, product_id, returned_units, quality_returns, returned_units / max(units_total, 1)),
            )
            review_volume = 8 + rng.randint(0, 7)
            for review_index in range(review_volume):
                feedback_count += 1
                if quality_incident and rng.random() < 0.62:
                    rating = rng.choice([1, 1, 2, 2, 3])
                    theme = rng.choice(["audio_dropout", "build_quality", "battery"])
                    text_by_theme = {
                        "audio_dropout": "The audio keeps cutting out after twenty minutes. Reconnecting helps briefly but the problem returns.",
                        "build_quality": "The hinge started clicking and feels loose after normal use. This does not feel durable.",
                        "battery": "Battery life has dropped sharply and no longer lasts through my commute.",
                    }
                    body = text_by_theme[theme]
                else:
                    rating = rng.choices([2, 3, 4, 5], weights=[5, 12, 38, 45])[0]
                    theme = rng.choice(["sound_quality", "comfort", "battery", "value"])
                    positive = {
                        "sound_quality": "Clear sound with balanced bass and good detail for everyday listening.",
                        "comfort": "Comfortable for long sessions and the ear cups fit well.",
                        "battery": "Battery easily lasts several days of normal use.",
                        "value": "Good value for the feature set and easy to recommend.",
                    }
                    body = positive[theme]
                feedback_id = f"FB-{product_id}-{month:%Y%m}-{review_index:03d}"
                raw_hash = hashlib.sha256(body.encode()).hexdigest()
                db.execute(
                    "INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        feedback_id,
                        product_id,
                        (month + timedelta(days=rng.randint(0, 27))).isoformat(),
                        rating,
                        (rating - 3) / 2,
                        theme,
                        f"{product_name} customer review",
                        body,
                        "commerce_reference_data",
                        1,
                        raw_hash,
                    ),
                )
    db.commit()
    manifest = {
        "dataset_version": dataset_version,
        "sales_aggregate_rows": sales_count,
        "sales_line_rows": sales_line_count,
        "feedback_rows": feedback_count,
    }
    manifest_path = path.parent / "source-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    db.close()
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--dataset-version", type=int, default=42)
    args = parser.parse_args()
    settings = get_settings()
    print(
        json.dumps(
            generate(settings.database_path, reset=args.reset, dataset_version=args.dataset_version),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
