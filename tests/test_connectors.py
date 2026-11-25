from __future__ import annotations

import os

import pytest

from backends import PsycopgBackend, unique_name
from whydiff.connectors import connect, get_columns
from whydiff.model import TableRef, TypeFamily

PG_URI = os.environ.get("WHYDIFF_TEST_PG_URI")


@pytest.mark.skipif(not PG_URI, reason="set WHYDIFF_TEST_PG_URI to run Postgres connector tests")
def test_postgres_connector_round_trip() -> None:
    assert PG_URI
    conn = connect(PG_URI)
    try:
        assert [tuple(r) for r in conn.execute("select 1, 'жук'")] == [(1, "жук")]
        columns = get_columns(conn, TableRef("pg_catalog", "pg_am"))
        assert columns[0].name == "oid"
        assert {c.family for c in columns} >= {TypeFamily.OTHER}
    finally:
        conn.close()


@pytest.mark.skipif(not PG_URI, reason="set WHYDIFF_TEST_PG_URI to run Postgres connector tests")
def test_postgres_connector_is_read_only() -> None:
    assert PG_URI
    table = unique_name("must_not_exist")
    conn = connect(PG_URI)
    try:
        with pytest.raises(Exception, match="read-only transaction"):
            conn.execute(f"create table {table} (x int)")
    finally:
        conn.close()
    # if the write had gone through, clean up so the next run is not misled
    writer = PsycopgBackend(PG_URI)
    try:
        exists = writer.execute(f"select count(*) from pg_tables where tablename = '{table}'")
        writer.execute(f"drop table if exists {table}")
    finally:
        writer.close()
    assert exists[0][0] == 0


def test_unknown_scheme() -> None:
    with pytest.raises(ValueError, match=r"Unsupported.*duckdb://"):
        connect("oracle://x")
    with pytest.raises(ValueError, match="URI"):
        connect("not a uri")
