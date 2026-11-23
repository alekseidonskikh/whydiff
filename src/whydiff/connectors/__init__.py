"""Connect to a database from a URI.

* ``postgresql://user@host:5432/db`` (also ``postgres://``, ``greenplum://``)
* ``duckdb:///path/to/file.duckdb`` or ``duckdb://`` for an in-memory database
"""

from __future__ import annotations

from whydiff.connectors.base import Connector, get_columns, run_query
from whydiff.log import get_logger, redact

log = get_logger("connect")


def connect(uri: str) -> Connector:
    scheme, sep, rest = uri.partition("://")
    if not sep:
        raise ValueError(
            f"Expected a URI like postgresql://... or duckdb:///file, got {redact(uri)!r}"
        )
    scheme = scheme.lower()
    conn: Connector
    if scheme in ("postgresql", "postgres", "greenplum"):
        from whydiff.connectors.postgres import PostgresConnector

        conn = PostgresConnector("postgresql://" + rest)
    elif scheme == "duckdb":
        from whydiff.connectors.duckdb import DuckDBConnector

        conn = DuckDBConnector(rest or ":memory:")
    else:
        raise ValueError(f"Unsupported database scheme {scheme!r}")
    log.info("connected to %s (%s)", redact(uri), conn.dialect.name)
    return conn


__all__ = ["Connector", "connect", "get_columns", "run_query"]
