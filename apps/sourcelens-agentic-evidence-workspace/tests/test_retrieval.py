from qdrant_client import QdrantClient

from sourcelens.retrieval import (
    LocalEvidenceIndex,
    QdrantEvidenceIndex,
    hash_embedding,
    index_feedback,
)


def test_hash_embedding_is_normalized():
    vector = hash_embedding("audio keeps cutting out")
    assert len(vector) == 256
    assert abs(sum(value * value for value in vector) - 1) < 1e-9


def test_qdrant_index_is_rebuildable(settings, investigator):
    # investigator fixture builds the local reference copy the index is rebuilt from.
    client = QdrantClient(":memory:")
    assert index_feedback(client, "feedback", settings.database_path) > 1000
    index = QdrantEvidenceIndex.__new__(QdrantEvidenceIndex)
    index.client = client
    index.collection = "feedback"
    index.fallback = LocalEvidenceIndex(settings.database_path)
    results = index.search("audio cutting out", product_id="NOVA-X300", limit=3)
    assert results
    assert all(item.metadata["product_id"] == "NOVA-X300" for item in results)
