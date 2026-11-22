"""Connect to a database from a URI.

* ``postgresql://user@host:5432/db`` (also ``postgres://``, ``greenplum://``)
* ``duckdb:///path/to/file.duckdb`` or ``duckdb://`` for an in-memory database
"""

from __future__ import annotations

from whydiff.connectors.base import Connector, get_columns


def connect(uri: str) -> Connector:
    scheme, sep, rest = uri.partition("://")
    if not sep:
        raise ValueError(f"Expected a URI like postgresql://... or duckdb:///file, got {uri!r}")
    scheme = scheme.lower()
    if scheme in ("postgresql", "postgres", "greenplum"):
        from whydiff.connectors.postgres import PostgresConnector

        return PostgresConnector("postgresql://" + rest)
    if scheme == "duckdb":
        from whydiff.connectors.duckdb import DuckDBConnector

        return DuckDBConnector(rest or ":memory:")
    raise ValueError(f"Unsupported database scheme {scheme!r}")


__all__ = ["Connector", "connect", "get_columns"]
