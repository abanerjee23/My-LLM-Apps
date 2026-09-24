from __future__ import annotations

from qdrant_client import QdrantClient

from sourcelens.config import get_settings
from sourcelens.retrieval import index_feedback


def main() -> None:
    settings = get_settings()
    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise SystemExit("Set QDRANT_URL and QDRANT_API_KEY before indexing Qdrant Cloud")
    client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    count = index_feedback(client, settings.qdrant_collection, settings.database_path)
    print(f"Indexed {count} feedback records into {settings.qdrant_collection}")


if __name__ == "__main__":
    main()
