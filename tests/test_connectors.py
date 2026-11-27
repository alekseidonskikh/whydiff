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


needs_pg = pytest.mark.skipif(not PG_URI, reason="set WHYDIFF_TEST_PG_URI")


def _with_options(uri: str, options: str) -> str:
    """Add libpq ``options`` (``-c name=value``) to a connection URI."""
    from urllib.parse import quote

    return f"{uri}{'&' if '?' in uri else '?'}options={quote(options)}"


@needs_pg
def test_session_settings_override_the_users() -> None:
    assert PG_URI
    uri = _with_options(PG_URI, "-c TimeZone=Europe/Moscow -c IntervalStyle=iso_8601")
    conn = connect(uri)
    try:
        assert conn.execute("show TimeZone")[0][0] == "UTC"
        assert conn.execute("show IntervalStyle")[0][0] == "postgres"
        assert conn.execute("show application_name")[0][0] == "whydiff"
    finally:
        conn.close()


@needs_pg
def test_backslash_cannot_end_a_string_literal() -> None:
    assert PG_URI
    # with standard_conforming_strings off, \\' would end the literal early
    conn = connect(_with_options(PG_URI, "-c standard_conforming_strings=off"))
    try:
        assert conn.execute("show standard_conforming_strings")[0][0] == "on"
        with pytest.raises(LookupError, match="not found"):
            get_columns(conn, TableRef("pg_catalog", "x\\' or true --"))
    finally:
        conn.close()


@needs_pg
def test_one_snapshot_for_the_whole_diff() -> None:
    assert PG_URI
    table = unique_name("snap")
    writer = PsycopgBackend(PG_URI)
    writer.execute(f"create table {table} (x int)")
    writer.execute(f"insert into {table} values (1)")
    # the snapshot is taken while connecting, so the data must exist by then
    reader = connect(PG_URI)
    live = connect(PG_URI, snapshot=False)
    try:
        assert reader.execute(f"select count(*) from {table}")[0][0] == 1
        writer.execute(f"insert into {table} values (2)")
        # the reader keeps the data as of its first query; without a snapshot
        # each query sees the latest data
        assert reader.execute(f"select count(*) from {table}")[0][0] == 1
        assert live.execute(f"select count(*) from {table}")[0][0] == 2
    finally:
        reader.close()
        live.close()
        writer.execute(f"drop table if exists {table}")
        writer.close()


@needs_pg
def test_statement_timeout() -> None:
    assert PG_URI
    conn = connect(PG_URI, statement_timeout=0.05)
    try:
        with pytest.raises(Exception, match="statement timeout"):
            conn.execute("select pg_sleep(2)")
    finally:
        conn.close()


def test_ignore_case_needs_unicode_lower() -> None:
    from whydiff.dialects.postgres import PostgresDialect
    from whydiff.model import Column
    from whydiff.normalize import NormalizationRules

    dialect = PostgresDialect()
    column = Column("name", "text", TypeFamily.TEXT)
    rules = NormalizationRules(case_insensitive=True)
    assert "lower(" in dialect.normalized_sql(column, rules)
    dialect.lower_template = None  # what the connector sets under the C locale without ICU
    with pytest.raises(ValueError, match="Unicode-aware lower"):
        dialect.normalized_sql(column, rules)
