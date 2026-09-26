from __future__ import annotations

import sqlite3

from google.cloud import bigquery

from sourcelens.config import get_settings
from sourcelens.query import ALLOWED_TABLES


def main() -> None:
    settings = get_settings()
    client = bigquery.Client(project=settings.google_cloud_project, location=settings.google_cloud_location)
    with sqlite3.connect(settings.database_path) as database:
        database.row_factory = sqlite3.Row
        for table in sorted(ALLOWED_TABLES):
            rows = [dict(row) for row in database.execute(f"SELECT * FROM {table}").fetchall()]
            target = f"{settings.google_cloud_project}.{settings.sourcelens_bigquery_dataset}.{table}"
            config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE", autodetect=True)
            job = client.load_table_from_json(rows, target, job_config=config)
            job.result()
            print(f"Loaded {len(rows)} rows into {target}")


if __name__ == "__main__":
    main()
