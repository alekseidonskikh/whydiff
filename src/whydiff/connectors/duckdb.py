from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from whydiff.dialects.duckdb import DuckDBDialect


class DuckDBConnector:
    """A DuckDB database file, opened read-only (or an in-memory database)."""

    def __init__(self, path: str = ":memory:", *, connection: Any = None) -> None:
        self.dialect = DuckDBDialect()
        if connection is not None:
            self._conn = connection
            return
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError("DuckDB support needs duckdb: pip install 'whydiff[duckdb]'") from exc
        read_only = path != ":memory:"
        self._conn = duckdb.connect(path, read_only=read_only)

    def execute(self, sql: str) -> Sequence[Sequence[Any]]:
        result: Sequence[Sequence[Any]] = self._conn.execute(sql).fetchall()
        return result

    def close(self) -> None:
        self._conn.close()
