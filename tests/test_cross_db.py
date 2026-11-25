"""The same data in different databases must hash and diff identically.

These tests need both Postgres and DuckDB; they run in CI.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest

from test_engine import DDL, Row, make_table, random_rows
from test_sql_parity import create_typed_table
from whydiff import NormalizationRules, Status, diff_tables
from whydiff.connectors import get_columns
from whydiff.model import TableRef

RULE_SETS = [
    NormalizationRules(),
    NormalizationRules(timestamp_precision=0, decimal_scale=1, float_digits=2, trim_strings=True),
]


@pytest.mark.parametrize("rules", RULE_SETS, ids=["default", "loose"])
@pytest.mark.parametrize("modulus", [1, 7, 7 * 32])
def test_same_data_has_same_checksums(
    postgres: Any, duckdb_backend: Any, rules: NormalizationRules, modulus: int
) -> None:
    """Every type family, NULLs and unicode: segment checksums match exactly."""
    sums = []
    for conn in (postgres, duckdb_backend):
        ref = TableRef(None, create_typed_table(conn))
        columns = get_columns(conn, ref)
        sql = conn.dialect.segment_checksums_query(ref, columns[:1], columns, rules, modulus, None)
        sums.append({int(b): (int(n), int(h)) for b, n, h in conn.execute(sql)})
    assert sums[0] == sums[1]


def test_identical_across_databases(postgres: Any, duckdb_backend: Any) -> None:
    rows = random_rows(random.Random(11), 3000)
    a = make_table(postgres, rows)
    b = make_table(duckdb_backend, list(reversed(rows)))
    for source, s, target, t in (
        (postgres, a, duckdb_backend, b),
        (duckdb_backend, b, postgres, a),
    ):
        result = diff_tables(source, s, target, t, ["id"], bucket_size=100)
        assert result.status is Status.IDENTICAL, result.buckets
        assert result.buckets.differing == 0


@pytest.mark.parametrize("seed", range(3))
def test_mutations_found_across_databases(postgres: Any, duckdb_backend: Any, seed: int) -> None:
    rng = random.Random(seed)
    rows = random_rows(rng, 2000)
    removed = set(rng.sample(range(2000), 5))
    edited = set(rng.sample([i for i in range(2000) if i not in removed], 15))
    target: list[Row] = [
        (r[0], r[1], Decimal("0.01") + (r[2] or 0), r[3], r[4]) if r[0] in edited else r
        for r in rows
        if r[0] not in removed
    ]
    target.append((99_999, "added", None, None, None))
    a, b = make_table(postgres, rows), make_table(duckdb_backend, target)
    result = diff_tables(
        postgres, a, duckdb_backend, b, ["id"], bucket_size=64, bisection_threshold=0, leaf_size=4
    )
    assert (result.removed, result.added, result.changed) == (5, 1, 15)
    assert [c.column for c in result.column_changes] == ["amount"]


def test_types_are_harmonized_across_databases(postgres: Any, duckdb_backend: Any) -> None:
    """integer in Postgres vs decimal(12,2) in DuckDB, same values: identical."""
    rows: list[Row] = [(i, f"n{i}", Decimal(i), None, i % 2 == 0) for i in range(200)]
    a = make_table(postgres, rows, DDL.replace("amount numeric(12,2)", "amount integer"))
    b = make_table(duckdb_backend, rows)
    result = diff_tables(postgres, a, duckdb_backend, b, ["id"])
    assert result.status is Status.IDENTICAL
    assert [m.column for m in result.schema_diff.type_mismatches] == ["amount"]
