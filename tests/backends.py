"""Writable test backends implementing the Connector protocol.

* DuckDB: in-memory, needs the duckdb package.
* Postgres: set WHYDIFF_TEST_PG_URI (uses psycopg), or WHYDIFF_TEST_PG_PSQL
  to a psql connection string (uses the psql binary, no Python driver needed).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import Sequence
from typing import Any

from whydiff.dialects import DuckDBDialect, PostgresDialect

NULL_MARK = "\x02NULL\x02"


class DuckDBBackend:
    def __init__(self) -> None:
        import duckdb

        self.dialect = DuckDBDialect()
        self._conn = duckdb.connect()

    def execute(self, sql: str) -> Sequence[Sequence[Any]]:
        cur = self._conn.execute(sql)
        return cur.fetchall() if cur.description else []

    def close(self) -> None:
        self._conn.close()


class PsycopgBackend:
    def __init__(self, uri: str) -> None:
        import psycopg

        self.dialect = PostgresDialect()
        self._conn = psycopg.connect(uri, autocommit=True)

    def execute(self, sql: str) -> Sequence[Sequence[Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql.encode(self._conn.info.encoding))
            return cur.fetchall() if cur.description else []

    def close(self) -> None:
        self._conn.close()


class PsqlBackend:
    """Runs SQL through the psql binary. Values come back as strings."""

    def __init__(self, conninfo: str) -> None:
        self.dialect = PostgresDialect()
        self._cmd = [
            "psql", conninfo, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
            "-F", "\x1f", "-R", "\x1e", "-P", f"null={NULL_MARK}",
        ]  # fmt: skip

    def execute(self, sql: str) -> Sequence[Sequence[Any]]:
        out = subprocess.run([*self._cmd, "-c", sql], check=True, capture_output=True, text=True)
        records = out.stdout.rstrip("\n").split("\x1e") if out.stdout.strip("\n") else []
        return [
            tuple(None if f == NULL_MARK else f for f in rec.lstrip("\n").split("\x1f"))
            for rec in records
            if rec.strip("\n")
        ]

    def close(self) -> None:
        pass


def postgres_backend() -> Any | None:
    if uri := os.environ.get("WHYDIFF_TEST_PG_URI"):
        return PsycopgBackend(uri)
    if conninfo := os.environ.get("WHYDIFF_TEST_PG_PSQL"):
        return PsqlBackend(conninfo)
    return None


def unique_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"
