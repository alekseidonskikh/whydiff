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
        # Connection.read_only only affects transactions psycopg opens itself,
        # and in autocommit mode it opens none, so it would be silently ignored.
        # The session default applies to every statement, autocommit or not.
        self._conn.execute(b"set default_transaction_read_only = on")

    def execute(self, sql: str) -> Sequence[Sequence[Any]]:
        # psycopg only takes literal strings, bytes or psycopg.sql objects as queries,
        # to steer code away from SQL built with f-strings. Our queries are built
        # by the dialect, with identifiers quoted and literals escaped, so they
        # are passed as bytes in the connection's encoding.
        query = sql.encode(self._conn.info.encoding)
        with self._conn.cursor() as cur:
            cur.execute(query)
            rows: Sequence[Sequence[Any]] = cur.fetchall()
            return rows

    def close(self) -> None:
        self._conn.close()
