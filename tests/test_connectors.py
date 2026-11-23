from __future__ import annotations

import os

import pytest

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
        with pytest.raises(Exception, match="read-only"):
            conn.execute("create table whydiff_must_fail (x int)")
    finally:
        conn.close()


def test_unknown_scheme() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        connect("oracle://x")
    with pytest.raises(ValueError, match="URI"):
        connect("not a uri")
