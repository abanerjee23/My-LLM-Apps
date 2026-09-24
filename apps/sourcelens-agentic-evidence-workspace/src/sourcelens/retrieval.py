from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .models import Evidence


def _tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"[a-z0-9]+", text.lower()))


def _similarity(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(value * b[token] for token, value in a.items())
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


class LocalEvidenceIndex:
    """Deterministic retrieval fallback; Qdrant uses the same evidence contract."""

    def __init__(self, database_path):
        self.database_path = database_path

    def search(self, query: str, *, product_id: str | None = None, limit: int = 8) -> list[Evidence]:
        sql = "SELECT * FROM feedback"
        params: list[Any] = []
        if product_id:
            sql += " WHERE product_id = ?"
            params.append(product_id)
        with sqlite3.connect(self.database_path) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(sql, params).fetchall()]
        query_tokens = _tokens(query)
        ranked = sorted(
            rows,
            key=lambda row: (_similarity(query_tokens, _tokens(row["body"])), -row["rating"]),
            reverse=True,
        )[:limit]
        return [
            Evidence(
                evidence_id=row["feedback_id"],
                kind="customer_feedback",
                source_id=row["feedback_id"],
                title=row["title"],
                excerpt=row["body"],
                metadata={
                    "product_id": row["product_id"],
                    "feedback_date": row["feedback_date"],
                    "rating": row["rating"],
                    "theme": row["theme"],
                    "source": row["source"],
                    "raw_hash": row["raw_hash"],
                },
            )
            for row in ranked
        ]


class QdrantEvidenceIndex:
    def __init__(self, *, url: str, api_key: str, collection: str, fallback: LocalEvidenceIndex):
        from qdrant_client import QdrantClient

        self.client = QdrantClient(url=url, api_key=api_key)
        self.collection = collection
        self.fallback = fallback

    def search(self, query: str, *, product_id: str | None = None, limit: int = 8) -> list[Evidence]:
        from qdrant_client import models

        query_filter = None
        if product_id:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="product_id", match=models.MatchValue(value=product_id)
                    )
                ]
            )
        try:
            response = self.client.query_points(
                collection_name=self.collection,
                query=hash_embedding(query),
                query_filter=query_filter,
                limit=limit,
                with_payload=True,
            )
        except Exception:
            return self.fallback.search(query, product_id=product_id, limit=limit)
        evidence = []
        for point in response.points:
            payload = point.payload or {}
            evidence.append(
                Evidence(
                    evidence_id=str(payload["feedback_id"]),
                    kind="customer_feedback",
                    source_id=str(payload["feedback_id"]),
                    title=str(payload["title"]),
                    excerpt=str(payload["body"]),
                    metadata={
                        key: value
                        for key, value in payload.items()
                        if key not in {"feedback_id", "title", "body"}
                    },
                )
            )
        return evidence or self.fallback.search(query, product_id=product_id, limit=limit)


EMBEDDING_DIMENSIONS = 256


def hash_embedding(text: str) -> list[float]:
    """A deterministic, free baseline embedding for a rebuildable evidence index."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token, count in _tokens(text).items():
        bucket = int.from_bytes(token.encode()[:4].ljust(4, b"\0"), "little") % len(vector)
        vector[bucket] += float(count)
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def index_feedback(client, collection: str, database_path) -> int:
    from qdrant_client import models

    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(
                size=EMBEDDING_DIMENSIONS, distance=models.Distance.COSINE
            ),
            metadata={"embedding": "sourcelens-hash-v1", "dimensions": EMBEDDING_DIMENSIONS},
        )
        client.create_payload_index(
            collection_name=collection,
            field_name="product_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
    with sqlite3.connect(database_path) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM feedback").fetchall()]
    for offset in range(0, len(rows), 200):
        batch = rows[offset : offset + 200]
        client.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, row["feedback_id"])),
                    vector=hash_embedding(f"{row['title']} {row['body']} {row['theme']}"),
                    payload=row,
                )
                for row in batch
            ],
            wait=True,
        )
    return len(rows)
