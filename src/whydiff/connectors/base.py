from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from whydiff.dialects.base import Dialect
from whydiff.model import Column, TableRef


class Connector(Protocol):
    """A read-only connection to one database."""

    @property
    def dialect(self) -> Dialect: ...

    def execute(self, sql: str) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


def get_columns(conn: Connector, table: TableRef) -> list[Column]:
    rows = conn.execute(conn.dialect.columns_query(table))
    if not rows:
        raise LookupError(f"Table {table} not found or has no columns")
    return [conn.dialect.column_from_row(str(r[0]), str(r[1]), r[2]) for r in rows]
