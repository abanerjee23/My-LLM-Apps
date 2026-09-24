from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

ALLOWED_TABLES = {
    "products",
    "sales_monthly",
    "sales_lines",
    "inventory_monthly",
    "returns_monthly",
    "feedback",
}
BLOCKED_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|MERGE|TRUNCATE|EXPORT|CALL|EXECUTE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class UnsafeQuery(ValueError):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    sql: str
    row_count: int


def validate_read_query(sql: str, *, dialect: str = "sqlite") -> str:
    if BLOCKED_PATTERN.search(sql) or ";" in sql.strip().rstrip(";"):
        raise UnsafeQuery("Only one read-only query is allowed")
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQuery(f"Invalid SQL: {exc}") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union, exp.With)):
        raise UnsafeQuery("Only SELECT or WITH queries are allowed")
    tables = {table.name for table in statements[0].find_all(exp.Table)}
    disallowed = tables - ALLOWED_TABLES
    if disallowed:
        raise UnsafeQuery(f"Tables are not allowlisted: {', '.join(sorted(disallowed))}")
    return sql.strip().rstrip(";")


class SQLiteWarehouse:
    def __init__(self, path):
        self.path = path

    def schema(self) -> dict[str, list[str]]:
        with sqlite3.connect(self.path) as db:
            result: dict[str, list[str]] = {}
            for table in sorted(ALLOWED_TABLES):
                result[table] = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        return result

    def run(self, sql: str, *, max_rows: int = 200) -> QueryResult:
        safe_sql = validate_read_query(sql)
        wrapped = f"SELECT * FROM ({safe_sql}) AS bounded_result LIMIT {int(max_rows)}"
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            cursor = db.execute(wrapped)
            rows = [dict(row) for row in cursor.fetchall()]
        return QueryResult(
            columns=list(rows[0].keys()) if rows else [],
            rows=rows,
            sql=safe_sql,
            row_count=len(rows),
        )


class BigQueryWarehouse:
    def __init__(self, project: str, dataset: str, location: str, max_bytes: int = 10 * 1024**3):
        from google.cloud import bigquery

        self.bigquery = bigquery
        self.client = bigquery.Client(project=project, location=location)
        self.project = project
        self.dataset = dataset
        self.max_bytes = max_bytes

    def qualify(self, sql: str) -> str:
        safe = validate_read_query(sql, dialect="bigquery")
        for table in sorted(ALLOWED_TABLES, key=len, reverse=True):
            safe = re.sub(
                rf"(?<![`.\w]){re.escape(table)}(?![`.\w])",
                f"`{self.project}.{self.dataset}.{table}`",
                safe,
            )
        return safe

    def dry_run(self, sql: str) -> int:
        qualified = self.qualify(sql)
        config = self.bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        return int(self.client.query(qualified, job_config=config).total_bytes_processed or 0)

    def run(self, sql: str, *, max_rows: int = 200) -> QueryResult:
        qualified = self.qualify(sql)
        estimated = self.dry_run(sql)
        if estimated > self.max_bytes:
            raise UnsafeQuery(f"Query exceeds scan limit: {estimated} bytes")
        config = self.bigquery.QueryJobConfig(maximum_bytes_billed=self.max_bytes)
        rows = [dict(row.items()) for row in self.client.query(qualified, job_config=config).result(max_results=max_rows)]
        return QueryResult(
            columns=list(rows[0].keys()) if rows else [], rows=rows, sql=sql, row_count=len(rows)
        )
