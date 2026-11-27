"""Diagnosis from normalized rows; no database needed."""

from __future__ import annotations

import random
from collections.abc import Sequence

import pytest

from whydiff.diagnose import Affected, Row, _ts, diagnose
from whydiff.model import TypeFamily as F
from whydiff.report.models import Diagnosis

NAMES = ["id", "value", "status"]


def run(
    family: F,
    pairs: Sequence[tuple[str | None, str | None]],
    unchanged: list[Row] | None = None,
) -> list[Diagnosis]:
    changed = [((str(i), x, "s"), (str(i), y, "s")) for i, (x, y) in enumerate(pairs)]
    rows = Affected(changed=changed, removed=[], added=[], unchanged=unchanged or [])
    return diagnose(NAMES, [F.INTEGER, family, F.TEXT], 1, rows)


def kinds(found: list[Diagnosis]) -> list[str]:
    return [d.kind for d in found]


def ts(hour: int, minute: int = 0, micro: str = "000000") -> str:
    return f"2026-03-01T{hour:02d}:{minute:02d}:00.{micro}"


def test_timezone_shift() -> None:
    found = run(F.TIMESTAMP, [(ts(h), ts(h + 3)) for h in range(5)])
    assert kinds(found) == ["timezone"]
    assert "+03:00:00" in found[0].message and found[0].rows == found[0].of_rows == 5


def test_non_timezone_time_shift() -> None:
    found = run(F.TIMESTAMP, [(ts(h, 0), ts(h, 7)) for h in range(5)])
    assert kinds(found) == ["time_shift"] and "+00:07:00" in found[0].message


def test_day_shift_of_timestamps() -> None:
    found = run(F.TIMESTAMP, [(ts(h), ts(h).replace("03-01", "03-02")) for h in range(5)])
    assert kinds(found) == ["time_shift"] and "+1 day" in found[0].message


def test_lost_timestamp_precision() -> None:
    found = run(F.TIMESTAMP, [(ts(h, micro="123456"), ts(h, micro="123000")) for h in range(4)])
    assert kinds(found) == ["time_precision"]
    assert "--timestamp-precision 3" in found[0].message


def test_timestamps_with_any_fraction_digits() -> None:
    # --timestamp-precision 1..5 leaves 1 to 5 fractional digits
    assert _ts("2026-03-01T10:00:00.12").microsecond == 120000
    assert _ts("2026-03-01T10:00:00").microsecond == 0
    found = run(F.TIMESTAMP, [(ts(h)[:22], ts(h + 3)[:22]) for h in range(5)])
    assert kinds(found) == ["timezone"]


def test_unreadable_values_never_fail_the_diff() -> None:
    assert run(F.FLOAT, [("Infinity", "1.000000"), ("Infinity", "2.000000")]) == []
    assert run(F.DECIMAL, [("NaN", "1.00"), ("2.00", "NaN"), ("Infinity", "-Infinity")]) == []
    bad = [(f"{i}", "not a time", "s") for i in range(10)]
    rows = Affected(changed=[], removed=bad[::2], added=[], unchanged=bad[1::2])
    assert diagnose(NAMES, [F.INTEGER, F.TIMESTAMP, F.TEXT], 1, rows) == []


def test_date_shift() -> None:
    found = run(F.DATE, [(f"2026-01-0{d}", f"2026-01-0{d + 1}") for d in range(1, 6)])
    assert kinds(found) == ["date_shift"] and "+1 day" in found[0].message


def test_rounding() -> None:
    found = run(F.DECIMAL, [("2.345", "2.350"), ("1.111", "1.110"), ("7.999", "8.000")])
    assert kinds(found) == ["rounding"] and "2 decimal places" in found[0].message


def test_scale() -> None:
    found = run(F.DECIMAL, [("12.50", "1250.00"), ("3.00", "300.00"), ("0.07", "7.00")])
    assert kinds(found) == ["scale"] and "100" in found[0].message


def test_offset_and_sign() -> None:
    assert kinds(run(F.INTEGER, [("10", "11"), ("20", "21"), ("5", "6")])) == ["offset"]
    assert kinds(run(F.INTEGER, [("10", "-10"), ("3", "-3")])) == ["sign"]


def test_float_noise() -> None:
    found = run(F.FLOAT, [("0.300000", "0.300001"), ("1.000000", "1.000001")])
    assert kinds(found) == ["float_noise"]
    assert "--float-digits 5 would make them equal" in found[0].message


def test_noise_suggests_digits_that_really_make_values_equal() -> None:
    # --float-digits counts significant digits: at 6 of them 1.000006 rounds
    # to 1.00001 while 1.000004 rounds to 1.00000; at 5 both are 1.0000
    found = run(F.FLOAT, [("1.000004e0", "1.000006e0"), ("2.000004e0", "2.000006e0")])
    assert "--float-digits 5 " in found[0].message


def test_noise_names_the_option_of_the_type() -> None:
    pairs = [
        ("1000.000001", "1000.000004"),
        ("2000.000002", "2000.000009"),
        ("5.000001", "5.000003"),
    ]
    found = run(F.DECIMAL, pairs)
    assert kinds(found) == ["float_noise"] and "--decimal-scale 4 " in found[0].message
    found = run(F.INTEGER, [("1000000", "1000001"), ("2000000", "2000003")])
    assert kinds(found) == ["float_noise"] and "--" not in found[0].message


def test_text_patterns() -> None:
    found = run(F.TEXT, [("abc ", "abc"), (" x", "x")])
    assert kinds(found) == ["whitespace"] and found[0].message.endswith("-> --trim")
    found = run(F.TEXT, [("abc", "abc\r"), ("x", "x\r")])  # --trim would not help
    assert kinds(found) == ["whitespace"] and "--trim strips spaces only" in found[0].message
    assert kinds(run(F.TEXT, [("Paris", "PARIS"), ("Rome", "rome")])) == ["case"]
    found = run(F.TEXT, [("abcdefgh", "abcde"), ("1234567", "12345")])
    assert kinds(found) == ["truncation"] and "target is cut to 5" in found[0].message


def test_null_patterns() -> None:
    assert kinds(run(F.TEXT, [("", None), (None, "")])) == ["empty_vs_null"]
    assert kinds(run(F.INTEGER, [("1", None), ("2", None)])) == ["set_to_null"]
    assert kinds(run(F.INTEGER, [(None, "1"), (None, "2")])) == ["filled_from_null"]


def test_inverted_boolean() -> None:
    assert kinds(run(F.BOOLEAN, [("1", "0"), ("0", "1"), ("1", "0")])) == ["inverted"]


def test_no_pattern_no_diagnosis() -> None:
    rng = random.Random(1)
    pairs = [(str(rng.randint(0, 999)), str(rng.randint(0, 999))) for _ in range(50)]
    assert run(F.INTEGER, [(x, y) for x, y in pairs if x != y]) == []


def test_one_row_is_not_a_pattern() -> None:
    assert run(F.TIMESTAMP, [(ts(1), ts(4))]) == []


def test_mixed_causes_below_threshold() -> None:
    pairs = [(ts(h), ts(h + 3)) for h in range(6)] + [(ts(h), ts(h, 5)) for h in range(6)]
    assert run(F.TIMESTAMP, pairs) == []


def test_swapped_columns() -> None:
    names = ["id", "first", "last"]
    changed = [((str(i), f"f{i}", f"l{i}"), (str(i), f"l{i}", f"f{i}")) for i in range(5)]
    rows = Affected(changed=changed, removed=[], added=[], unchanged=[])
    found = diagnose(names, [F.INTEGER, F.TEXT, F.TEXT], 1, rows)
    assert "swapped" in kinds(found)


def _table(n: int) -> list[Row]:
    return [
        (str(i), f"2026-01-01T{i % 24:02d}:00:00.000000", ["a", "b", "c"][i % 3]) for i in range(n)
    ]


def test_contiguous_key_range() -> None:
    rows = _table(300)
    removed = [r for r in rows if 100 <= int(r[0] or 0) < 120]
    unchanged = [r for r in rows if r not in removed]
    found = diagnose(
        ["id", "ts", "status"],
        [F.INTEGER, F.TIMESTAMP, F.TEXT],
        1,
        Affected(changed=[], removed=removed, added=[], unchanged=unchanged),
    )
    assert [(d.kind, d.scope, d.column) for d in found] == [("range", "removed", "id")]
    assert "100 .. 119" in found[0].message


def test_concentrated_in_one_value() -> None:
    rows = _table(300)
    removed = [r for r in rows if r[2] == "b"][::3]
    unchanged = [r for r in rows if r not in removed]
    found = diagnose(
        ["id", "ts", "status"],
        [F.INTEGER, F.TIMESTAMP, F.TEXT],
        1,
        Affected(changed=[], removed=removed, added=[], unchanged=unchanged),
    )
    concentrated = [d for d in found if d.kind == "concentrated"]
    assert [d.column for d in concentrated] == ["status"]
    assert "'b'" in concentrated[0].message


@pytest.mark.parametrize("n", [0, 1, 4])
def test_too_few_rows_for_concentration(n: int) -> None:
    rows = _table(100)
    rows_affected = rows[:n]
    found = diagnose(
        ["id", "ts", "status"],
        [F.INTEGER, F.TIMESTAMP, F.TEXT],
        1,
        Affected(changed=[], removed=rows_affected, added=[], unchanged=rows[n:]),
    )
    assert found == []
