from __future__ import annotations

import io
import logging
import random
from typing import Any

import pytest

from test_engine import make_table, random_rows
from whydiff import diff_tables
from whydiff.cli import main
from whydiff.log import configure_logging, get_logger, redact, shorten_sql


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("postgresql://bob:s3cr3t@db:5432/x", "postgresql://bob:***@db:5432/x"),
        ("postgresql://bob@db/x", "postgresql://bob@db/x"),
        ("greenplum://u:p%40ss@h/x?sslmode=require", "greenplum://u:***@h/x?sslmode=require"),
        ("host=db password=s3cr3t user=bob", "host=db password=*** user=bob"),
        ("host=db password='a b c' user=bob", "host=db password=*** user=bob"),
        ("duckdb:///tmp/x.duckdb", "duckdb:///tmp/x.duckdb"),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_shorten_sql() -> None:
    assert shorten_sql("select\n   1") == "select 1"
    long = shorten_sql("x" * 50, limit=10)
    assert long.startswith("x" * 10) and "40 more characters" in long


def test_library_is_silent_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    get_logger("engine").warning("not shown")
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out == ""


def test_diff_logs_each_stage(backend: Any, caplog: pytest.LogCaptureFixture) -> None:
    rows = random_rows(random.Random(7), 20_000)
    target = [(r[0], "edited", r[2], r[3], r[4]) if r[0] % 997 == 0 else r for r in rows]
    a, b = make_table(backend, rows), make_table(backend, target)
    with caplog.at_level(logging.DEBUG, logger="whydiff"):
        diff_tables(backend, a, backend, b, ["id"])
    messages = [r.getMessage() for r in caplog.records]
    text = "\n".join(messages)
    for expected in (
        "diff ",
        "schema:",
        "rows:",
        "pass 1:",
        "bisection level 1",
        "fetched",
        "different:",
    ):
        assert expected in text, expected
    sql = [m for m in messages if m.startswith("source checksums")]
    assert any("select" in m for m in sql) and any(" rows in " in m for m in sql)


def test_configure_logging_levels() -> None:
    stream = io.StringIO()
    configure_logging(1, stream=stream)
    get_logger("x").info("hello")
    get_logger("x").debug("hidden")
    configure_logging(0, stream=stream)
    get_logger("x").info("hidden too")
    out = stream.getvalue()
    assert "INFO  hello" in out and "hidden" not in out


def test_quiet_logs_errors_only() -> None:
    stream = io.StringIO()
    configure_logging(-1, stream=stream)
    get_logger("x").warning("hidden")
    get_logger("x").error("shown")
    out = stream.getvalue()
    assert "shown" in out and "hidden" not in out


def test_cli_errors_hide_passwords(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["diff", "oracle://bob:s3cr3t@db/x", "t", "duckdb://", "t", "-k", "id"])
    err = capsys.readouterr().err
    assert code == 2
    assert "s3cr3t" not in err and "Unsupported" in err
