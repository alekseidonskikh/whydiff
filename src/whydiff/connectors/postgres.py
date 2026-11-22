from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from whydiff.dialects.postgres import PostgresDialect


class PostgresConnector:
    """Postgres and Greenplum through psycopg 3. Sessions are read-only."""

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "Postgres support needs psycopg: pip install 'whydiff[postgres]'"
            ) from exc
        self.dialect = PostgresDialect()
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._conn.read_only = True

    def execute(self, sql: str) -> Sequence[Sequence[Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql)  # type: ignore[arg-type,unused-ignore]
            rows: Sequence[Sequence[Any]] = cur.fetchall()
            return rows

    def close(self) -> None:
        self._conn.close()
