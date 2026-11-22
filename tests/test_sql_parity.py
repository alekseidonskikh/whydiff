"""SQL generated for each database must match the Python reference exactly."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from backends import unique_name
from whydiff.connectors import get_columns
from whydiff.model import TableRef
from whydiff.normalize import NormalizationRules, normalize_value, row_hash

MSK = dt.timezone(dt.timedelta(hours=3))
UTC = dt.timezone.utc

ROWS: list[tuple[Any, ...]] = [
    (1, Decimal("1.50"), 0.1, "hello", True, dt.date(2024, 1, 2),
     dt.datetime(2024, 1, 2, 3, 4, 5, 123456), dt.datetime(2024, 1, 2, 3, 4, 5, 999999, MSK)),
    (2, Decimal("0.00"), -2.675, "", False, dt.date(1999, 12, 31),
     dt.datetime(2024, 1, 2, 3, 4, 5), dt.datetime(2024, 6, 30, 23, 0, tzinfo=UTC)),
    (3, None, None, None, None, None, None, None),
    (4, Decimal("123456789012.35"), 1e-7, "  пробел ' кавычка | разделитель  ", True,
     dt.date(2024, 2, 29), dt.datetime(2000, 1, 1), dt.datetime(2024, 3, 31, 1, 30, tzinfo=MSK)),
    (5, Decimal("-7.01"), 3.0, "N", False, dt.date(2024, 1, 1),
     dt.datetime(2024, 12, 31, 23, 59, 59, 1), dt.datetime(2024, 12, 31, 23, 59, 59, 1, UTC)),
]  # fmt: skip

RULE_SETS = [
    NormalizationRules(),
    NormalizationRules(
        timestamp_precision=0,
        decimal_scale=1,
        float_digits=2,
        trim_strings=True,
        case_insensitive=True,
    ),
]


def literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def create_typed_table(conn: Any) -> str:
    name = unique_name("parity")
    double = "double" if conn.dialect.name == "duckdb" else "double precision"
    conn.execute(
        f"create table {name} (id integer, n numeric(20,2), f {double}, s text, b boolean, "
        "d date, ts timestamp, tz timestamp with time zone)"
    )
    values = ", ".join("(" + ", ".join(literal(v) for v in r) + ")" for r in ROWS)
    conn.execute(f"insert into {name} values {values}")
    return name


@pytest.mark.parametrize("rules", RULE_SETS, ids=["default", "loose"])
def test_normalized_values_and_hashes_match_reference(
    backend: Any, rules: NormalizationRules
) -> None:
    table = create_typed_table(backend)
    columns = get_columns(backend, TableRef(None, table))
    d = backend.dialect
    select = ", ".join(d.normalized_sql(c, rules) for c in columns)
    got = backend.execute(
        f"select {select}, {d.row_hash_sql(columns, rules)} from {table} order by id"
    )
    assert len(got) == len(ROWS)
    for expected_row, got_row in zip(ROWS, got, strict=True):
        want = [normalize_value(v, c, rules) for v, c in zip(expected_row, columns, strict=True)]
        assert [None if v is None else str(v) for v in got_row[:-1]] == want
        assert int(got_row[-1]) == row_hash(expected_row, columns, rules)
