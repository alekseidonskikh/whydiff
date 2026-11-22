from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal
from typing import Any

import pytest

from backends import unique_name
from whydiff import NormalizationRules, Status, diff_tables, render_text

Row = tuple[int, str | None, Decimal | None, dt.datetime | None, bool | None]
DDL = "(id integer, name text, amount numeric(12,2), created_at timestamp, active boolean)"


def literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, dt.datetime):
        return f"'{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def make_table(conn: Any, rows: list[Row], ddl: str = DDL) -> str:
    name = unique_name("t")
    conn.execute(f"create table {name} {ddl}")
    for i in range(0, len(rows), 1000):
        chunk = rows[i : i + 1000]
        values = ", ".join("(" + ", ".join(literal(v) for v in r) + ")" for r in chunk)
        conn.execute(f"insert into {name} values {values}")
    return name


def random_rows(rng: random.Random, n: int) -> list[Row]:
    base = dt.datetime(2024, 1, 1)
    return [
        (
            i,
            rng.choice([None, "", f"name {i}", "Ж" * rng.randint(1, 3)]),
            None if rng.random() < 0.1 else Decimal(rng.randint(-(10**6), 10**6)) / 100,
            base + dt.timedelta(seconds=rng.randint(0, 10**7), microseconds=rng.randint(0, 999999)),
            rng.choice([True, False, None]),
        )
        for i in range(n)
    ]


def test_identical_tables(backend: Any) -> None:
    rows = random_rows(random.Random(1), 500)
    a, b = make_table(backend, rows), make_table(backend, list(reversed(rows)))
    result = diff_tables(backend, a, backend, b, ["id"], bucket_size=50)
    assert result.status is Status.IDENTICAL
    assert result.buckets.total == 10
    assert result.buckets.differing == 0
    assert (result.removed, result.added, result.changed) == (0, 0, 0)


def test_finds_each_kind_of_difference(backend: Any) -> None:
    rows = random_rows(random.Random(2), 300)
    target = [r for r in rows if r[0] != 10]  # removed
    target.append((1000, "new", Decimal("1.00"), None, True))  # added
    target = [
        (r[0], r[1], Decimal("999.99"), r[3], r[4]) if r[0] == 20 else r for r in target
    ]  # changed amount
    a, b = make_table(backend, rows), make_table(backend, target)
    result = diff_tables(backend, a, backend, b, ["id"], bucket_size=25)
    assert result.status is Status.DIFFERENT
    assert (result.removed, result.added, result.changed) == (1, 1, 1)
    assert [c.column for c in result.column_changes] == ["amount"]
    changed = next(s for s in result.samples if s.kind == "changed")
    assert changed.key == {"id": "20"}
    assert changed.target and changed.target["amount"] == "999.99"
    assert "amount" in render_text(result)


def test_null_vs_empty_string_is_a_change(backend: Any) -> None:
    a = make_table(backend, [(1, None, None, None, None)])
    b = make_table(backend, [(1, "", None, None, None)])
    result = diff_tables(backend, a, backend, b, ["id"])
    assert result.changed == 1
    assert result.column_changes[0].column == "name"


def test_timestamp_precision_rule(backend: Any) -> None:
    ts = dt.datetime(2024, 5, 1, 12, 0, 0, 123456)
    a = make_table(backend, [(1, "x", None, ts, True)])
    b = make_table(backend, [(1, "x", None, ts.replace(microsecond=123000), True)])
    assert diff_tables(backend, a, backend, b, ["id"]).changed == 1
    loose = NormalizationRules(timestamp_precision=3)
    assert diff_tables(backend, a, backend, b, ["id"], rules=loose).status is Status.IDENTICAL


def test_exclude_where_and_schema(backend: Any) -> None:
    rows = random_rows(random.Random(3), 100)
    a = make_table(backend, rows)
    b = make_table(
        backend,
        [(r[0], "renamed", r[2], r[3], r[4], 1) for r in rows],
        DDL[:-1] + ", extra integer)",
    )
    result = diff_tables(backend, a, backend, b, ["id"], exclude=["name"], where="id < 50")
    assert result.counts.source == 50
    assert result.schema_diff.only_in_target == ["extra"]
    assert result.changed == 0
    assert result.status is Status.DIFFERENT  # because of the schema difference


def test_composite_key_and_type_harmonization(backend: Any) -> None:
    a = make_table(backend, [(1, "a", Decimal("5"), None, True)], "(id integer, name text, "
                   "amount integer, created_at timestamp, active boolean)")  # fmt: skip
    b = make_table(backend, [(1, "a", Decimal("5.00"), None, True)])
    result = diff_tables(backend, a, backend, b, ["id", "name"])
    assert result.status is Status.IDENTICAL
    assert result.schema_diff.type_mismatches[0].column == "amount"


def test_row_budget_truncates(backend: Any) -> None:
    rows = random_rows(random.Random(4), 400)
    changed = [(r[0], "changed", r[2], r[3], r[4]) for r in rows]
    a, b = make_table(backend, rows), make_table(backend, changed)
    result = diff_tables(backend, a, backend, b, ["id"], bucket_size=40, max_rows=100)
    assert result.truncated
    assert result.buckets.inspected < result.buckets.segments
    assert 0 < result.changed < 400


def test_bisection_downloads_far_fewer_rows(backend: Any) -> None:
    rng = random.Random(5)
    rows = random_rows(rng, 20_000)
    edited = set(rng.sample(range(20_000), 20))
    target = [(r[0], "edited", r[2], r[3], r[4]) if r[0] in edited else r for r in rows]
    a, b = make_table(backend, rows), make_table(backend, target)

    plain = diff_tables(backend, a, backend, b, ["id"], max_bisection_levels=0)
    bisected = diff_tables(backend, a, backend, b, ["id"])

    assert plain.changed == bisected.changed == 20
    assert plain.buckets.bisection_levels == 0
    assert bisected.buckets.bisection_levels >= 1
    assert 0 < bisected.buckets.segments <= 20  # two edits may share a segment
    assert bisected.buckets.rows_downloaded * 10 < plain.buckets.rows_downloaded
    assert "bisect" in render_text(bisected)


def test_bisection_skipped_when_download_is_cheap(backend: Any) -> None:
    rows = random_rows(random.Random(6), 300)
    target = [(r[0], "x", r[2], r[3], r[4]) if r[0] == 5 else r for r in rows]
    a, b = make_table(backend, rows), make_table(backend, target)
    result = diff_tables(backend, a, backend, b, ["id"])
    assert result.buckets.bisection_levels == 0
    assert result.changed == 1


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("bisect", [False, True], ids=["plain", "bisect"])
def test_random_mutations_are_found_exactly(backend: Any, seed: int, bisect: bool) -> None:
    """Apply random removals, additions and edits; the diff must report exactly them."""
    rng = random.Random(seed)
    rows = random_rows(rng, rng.randint(50, 800))
    by_id = {r[0]: r for r in rows}
    ids = list(by_id)
    removed = set(rng.sample(ids, rng.randint(0, 10)))
    edited = set(rng.sample([i for i in ids if i not in removed], rng.randint(0, 20)))
    added = {10_000 + i for i in range(rng.randint(0, 10))}

    target: list[Row] = []
    real_edits = 0
    for i, r in by_id.items():
        if i in removed:
            continue
        if i in edited:
            new = (i, f"edited {rng.random()}", r[2], r[3], r[4])
            real_edits += new != r
            target.append(new)
        else:
            target.append(r)
    target += [(i, "added", None, None, None) for i in added]
    rng.shuffle(target)

    a, b = make_table(backend, rows), make_table(backend, target)
    options: dict[str, Any] = {"bucket_size": rng.choice([1, 7, 64, 5000])}
    if bisect:  # force deep bisection even on small tables
        options.update(
            bisection_threshold=0,
            leaf_size=rng.choice([1, 4, 16]),
            bisection_factor=rng.choice([2, 3, 32]),
        )
    else:
        options["max_bisection_levels"] = 0
    result = diff_tables(backend, a, backend, b, ["id"], **options)
    assert (result.removed, result.added, result.changed) == (len(removed), len(added), real_edits)
    expected = Status.IDENTICAL if not (removed or added or real_edits) else Status.DIFFERENT
    assert result.status is expected
