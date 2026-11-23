from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any, Protocol

from whydiff.dialects.base import Dialect
from whydiff.log import get_logger, shorten_sql
from whydiff.model import Column, TableRef

log = get_logger("sql")


class Connector(Protocol):
    """A read-only connection to one database."""

    @property
    def dialect(self) -> Dialect: ...

    def execute(self, sql: str) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


def run_query(conn: Connector, sql: str, label: str) -> Sequence[Sequence[Any]]:
    """Execute ``sql``, logging it at DEBUG with its duration and row count.

    ``label`` names the query in logs, e.g. ``"source checksums"``.
    """
    if not log.isEnabledFor(logging.DEBUG):
        return conn.execute(sql)
    log.debug("%s: %s", label, shorten_sql(sql))
    started = time.perf_counter()
    try:
        rows = conn.execute(sql)
    except Exception:
        log.debug("%s failed after %.3fs", label, time.perf_counter() - started)
        raise
    log.debug("%s: %s rows in %.3fs", label, f"{len(rows):,}", time.perf_counter() - started)
    return rows


def get_columns(conn: Connector, table: TableRef, label: str = "columns") -> list[Column]:
    rows = run_query(conn, conn.dialect.columns_query(table), label)
    if not rows:
        raise LookupError(f"Table {table} not found or has no columns")
    return [conn.dialect.column_from_row(str(r[0]), str(r[1]), r[2]) for r in rows]
