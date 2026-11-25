"""Explain *why* two tables differ.

The engine downloads whole differing segments, so besides the changed,
removed and added rows it also has unchanged rows from the same segments: a
random sample (segments are chosen by key hash) to compare against.

Two kinds of findings:

* **Value patterns**, per changed column, from the (source, target) pairs:
  a constant time shift (a time zone when it is whole hours), lost timestamp
  precision, rounding, a scale factor (cents vs units), a constant offset,
  trimmed or case-changed or truncated text, ``''`` vs NULL, values set to or
  filled from NULL, inverted booleans, swapped columns.
* **Concentration**: the affected rows sit in a contiguous key range, a
  narrow time window, or share one value of a column that is spread out in
  the unchanged rows.

A finding is reported only when it explains nearly all affected rows (see
:mod:`whydiff.defaults`), so a coincidence in one row is never a diagnosis.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

from whydiff import defaults
from whydiff.model import TypeFamily
from whydiff.report.models import Diagnosis

Row = tuple[str | None, ...]
Pair = tuple[str | None, str | None]

_NUMERIC = (TypeFamily.INTEGER, TypeFamily.DECIMAL, TypeFamily.FLOAT)
_TIME = (TypeFamily.TIMESTAMP, TypeFamily.TIMESTAMPTZ)
_ORDERED = (*_NUMERIC, *_TIME, TypeFamily.DATE)


@dataclass(frozen=True)
class Affected:
    """Rows of one kind of difference, as the engine found them."""

    changed: Sequence[tuple[Row, Row]]  # (source row, target row)
    removed: Sequence[Row]  # source rows
    added: Sequence[Row]  # target rows
    unchanged: Sequence[Row]  # rows equal on both sides, from the same segments


def diagnose(
    names: Sequence[str],
    families: Sequence[TypeFamily],
    n_keys: int,
    rows: Affected,
    *,
    min_share: float = defaults.DIAGNOSIS_MIN_SHARE,
    min_rows: int = defaults.DIAGNOSIS_MIN_ROWS,
) -> list[Diagnosis]:
    found: list[Diagnosis] = []
    columns = list(range(n_keys, len(names)))

    # value patterns, per changed column
    changed_by_col: dict[int, list[Pair]] = {}
    for a, b in rows.changed:
        for i in columns:
            if a[i] != b[i]:
                changed_by_col.setdefault(i, []).append((a[i], b[i]))
    for i, pairs in changed_by_col.items():
        if len(pairs) < min_rows:
            continue
        for check in _checks_for(families[i]):
            d = _try(check, pairs, min_share)
            if d is not None:
                found.append(d.model_copy(update={"column": names[i], "of_rows": len(pairs)}))
                break
    found += _swapped(names, families, rows.changed, changed_by_col, min_share, min_rows)

    # concentration: per changed column (one cause per column is the common case),
    # then for removed and added rows
    groups: list[tuple[str, str, list[Row]]] = [
        ("changed", f"rows with a changed {names[i]}", [a for a, b in rows.changed if a[i] != b[i]])
        for i in changed_by_col
    ]
    groups += [("removed", "removed rows", list(rows.removed))]
    groups += [("added", "added rows", list(rows.added))]
    for scope, noun, affected in groups:
        if len(affected) >= max(min_rows, defaults.CONCENTRATION_MIN_ROWS):
            found += _concentration(scope, noun, names, families, n_keys, affected, rows.unchanged)
    return found


# -- value patterns ---------------------------------------------------------------

Check = Callable[[list[Pair]], tuple[int, str, str] | None]
"""Returns (rows explained, kind, message) for the dominant pattern, or None."""


def _try(check: Check, pairs: list[Pair], min_share: float) -> Diagnosis | None:
    outcome = check(pairs)
    if outcome is None:
        return None
    explained, kind, message = outcome
    if explained < len(pairs) * min_share:
        return None
    return Diagnosis(
        kind=kind, scope="changed", rows=explained, of_rows=len(pairs), message=message
    )


def _checks_for(family: TypeFamily) -> list[Check]:
    common: list[Check] = [_empty_vs_null, _set_to_null, _filled_from_null]
    if family in _TIME:
        return [*common, _time_precision, _time_shift]
    if family is TypeFamily.DATE:
        return [*common, _date_shift]
    if family is TypeFamily.FLOAT:  # tiny differences are the usual story for floats
        return [*common, _float_noise, _rounding, _scale, _sign, _offset]
    if family in _NUMERIC:
        return [*common, _rounding, _scale, _sign, _offset, _float_noise]
    if family is TypeFamily.TEXT:
        return [*common, _whitespace, _case, _truncation]
    if family is TypeFamily.BOOLEAN:
        return [*common, _inverted]
    return common


def _count(pairs: list[Pair], test: Callable[[str, str], bool]) -> int:
    n = 0
    for x, y in pairs:
        if x is None or y is None:
            continue
        with suppress(ValueError, InvalidOperation, ZeroDivisionError):
            n += bool(test(x, y))
    return n


def _empty_vs_null(pairs: list[Pair]) -> tuple[int, str, str] | None:
    n = sum(1 for x, y in pairs if {x, y} == {None, ""})
    return (n, "empty_vs_null", "'' on one side, NULL on the other") if n else None


def _set_to_null(pairs: list[Pair]) -> tuple[int, str, str] | None:
    n = sum(1 for x, y in pairs if x not in (None, "") and y is None)
    return (n, "set_to_null", "NULL in target where source has a value") if n else None


def _filled_from_null(pairs: list[Pair]) -> tuple[int, str, str] | None:
    n = sum(1 for x, y in pairs if x is None and y not in (None, ""))
    return (n, "filled_from_null", "a value in target where source has NULL") if n else None


def _most_common(values: list[object]) -> tuple[object, int] | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0]


# time


def _ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def _fmt_delta(delta: dt.timedelta) -> str:
    sign = "-" if delta < dt.timedelta(0) else "+"
    delta = abs(delta)
    hours, rest = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if delta.microseconds:
        text += f".{delta.microseconds:06d}".rstrip("0")
    if delta.days:
        text = f"{delta.days} day{'s' if delta.days != 1 else ''} {text}"
    return sign + text


def _time_shift(pairs: list[Pair]) -> tuple[int, str, str] | None:
    deltas: list[object] = []
    for x, y in pairs:
        if x is not None and y is not None:
            deltas.append(_ts(y) - _ts(x))
    top = _most_common(deltas)
    if top is None:
        return None
    delta, n = top
    assert isinstance(delta, dt.timedelta)
    text = f"shifted by exactly {_fmt_delta(delta)}"
    whole_hours = delta.microseconds == 0 and delta.seconds % 1800 == 0
    if whole_hours and abs(delta) <= dt.timedelta(hours=14):
        return n, "timezone", f"{text} -> likely a time zone mismatch"
    if delta.microseconds == 0 and delta.seconds == 0:
        return n, "time_shift", f"{text} -> likely an off-by-days error"
    return n, "time_shift", text


def _time_precision(pairs: list[Pair]) -> tuple[int, str, str] | None:
    # the most digits that still explain the most rows: the precision target kept
    best: tuple[int, str, str] | None = None
    for digits in range(0, 6):
        width = 19 if digits == 0 else 20 + digits

        def truncated(x: str, y: str, width: int = width) -> bool:
            return x[:width] == y[:width] and set(y[width:].lstrip(".")) <= {"0"}

        n = _count(pairs, truncated)
        if n and (best is None or n >= best[0]):
            best = (
                n,
                "time_precision",
                f"target keeps {digits} fractional second digits"
                f" -> --timestamp-precision {digits} would make them equal",
            )
    return best


def _date_shift(pairs: list[Pair]) -> tuple[int, str, str] | None:
    days: list[object] = []
    for x, y in pairs:
        if x is not None and y is not None:
            days.append((dt.date.fromisoformat(y) - dt.date.fromisoformat(x)).days)
    top = _most_common(days)
    if top is None:
        return None
    d, n = top
    assert isinstance(d, int)
    return n, "date_shift", f"shifted by exactly {d:+d} day{'s' if abs(d) != 1 else ''}"


# numbers


def _rounding(pairs: list[Pair]) -> tuple[int, str, str] | None:
    def places(x: str) -> int:
        exponent = Decimal(x).normalize().as_tuple().exponent
        return max(0, -exponent) if isinstance(exponent, int) else 0

    most = max((places(x) for x, _ in pairs if x is not None), default=0)
    best: tuple[int, str, str] | None = None
    for digits in range(0, most):
        q = Decimal(1).scaleb(-digits)
        for mode, word in ((ROUND_HALF_UP, "rounded"), (ROUND_DOWN, "truncated")):

            def same(x: str, y: str, q: Decimal = q, mode: str = mode) -> bool:
                return Decimal(x).quantize(q, rounding=mode) == Decimal(y)

            n = _count(pairs, same)
            if n and (best is None or n > best[0]):
                best = (n, "rounding", f"target is source {word} to {digits} decimal places")
    return best


def _scale(pairs: list[Pair]) -> tuple[int, str, str] | None:
    ratios: list[object] = []
    for x, y in pairs:
        if x is None or y is None or Decimal(x) == 0:
            continue
        ratios.append((Decimal(y) / Decimal(x)).normalize())
    top = _most_common(ratios)
    if top is None:
        return None
    ratio, n = top
    assert isinstance(ratio, Decimal)
    for k in range(1, 7):
        if ratio == Decimal(10) ** k:
            return n, "scale", f"target is source multiplied by {10**k:,} -> likely a unit change"
        if ratio == Decimal(10) ** -k:
            return n, "scale", f"target is source divided by {10**k:,} -> likely a unit change"
    return None


def _sign(pairs: list[Pair]) -> tuple[int, str, str] | None:
    n = _count(pairs, lambda x, y: Decimal(x) == -Decimal(y) != 0)
    return (n, "sign", "sign flipped") if n else None


def _offset(pairs: list[Pair]) -> tuple[int, str, str] | None:
    deltas: list[object] = []
    for x, y in pairs:
        if x is not None and y is not None:
            deltas.append((Decimal(y) - Decimal(x)).normalize())
    top = _most_common(deltas)
    if top is None:
        return None
    delta, n = top
    assert isinstance(delta, Decimal)
    return n, "offset", f"target = source {'+' if delta > 0 else '-'} {abs(delta):f}"


def _float_noise(pairs: list[Pair]) -> tuple[int, str, str] | None:
    worst = Decimal(0)
    n = 0
    for x, y in pairs:
        if x is None or y is None:
            continue
        diff = abs(Decimal(y) - Decimal(x))
        scale = max(abs(Decimal(x)), Decimal(1))
        if diff / scale <= Decimal("1e-4"):
            n += 1
            worst = max(worst, diff)
    if not n:
        return None
    exponent = worst.adjusted() if worst else 0
    digits = max(0, -exponent - 1)
    return (
        n,
        "float_noise",
        f"tiny differences (at most {worst:f}) -> --float-digits {digits} would make them equal",
    )


# text and booleans


def _whitespace(pairs: list[Pair]) -> tuple[int, str, str] | None:
    n = _count(pairs, lambda x, y: x.strip() == y.strip())
    return (n, "whitespace", "differs only in surrounding spaces -> --trim") if n else None


def _case(pairs: list[Pair]) -> tuple[int, str, str] | None:
    n = _count(pairs, lambda x, y: x.lower() == y.lower())
    return (n, "case", "differs only in letter case -> --ignore-case") if n else None


def _truncation(pairs: list[Pair]) -> tuple[int, str, str] | None:
    cuts: Counter[tuple[int, str]] = Counter()
    for x, y in pairs:
        if x is None or y is None or len(x) == len(y):
            continue
        short, long, side = (y, x, "target") if len(y) < len(x) else (x, y, "source")
        if long.startswith(short):
            cuts[(len(short), side)] += 1
    if not cuts:
        return None
    (length, side), n = cuts.most_common(1)[0]
    return n, "truncation", f"{side} is cut to {length} characters -> likely a column too short"


def _inverted(pairs: list[Pair]) -> tuple[int, str, str] | None:
    n = sum(1 for x, y in pairs if {x, y} == {"0", "1"})
    return (n, "inverted", "true and false swapped") if n else None


# swapped columns


def _swapped(
    names: Sequence[str],
    families: Sequence[TypeFamily],
    changed: Sequence[tuple[Row, Row]],
    by_col: dict[int, list[Pair]],
    min_share: float,
    min_rows: int,
) -> list[Diagnosis]:
    found = []
    cols = sorted(by_col)
    for pos, i in enumerate(cols):
        for j in cols[pos + 1 :]:
            if families[i] is not families[j]:
                continue
            both = [(a, b) for a, b in changed if a[i] != b[i] and a[j] != b[j]]
            n = sum(1 for a, b in both if a[i] == b[j] and a[j] == b[i])
            if n >= min_rows and n >= max(len(by_col[i]), len(by_col[j])) * min_share:
                found.append(
                    Diagnosis(
                        kind="swapped",
                        scope="changed",
                        column=f"{names[i]}, {names[j]}",
                        rows=n,
                        of_rows=len(by_col[i]),
                        message=f"values of {names[i]} and {names[j]} are swapped",
                    )
                )
    return found


# -- concentration ------------------------------------------------------------------


def _number(value: str | None, family: TypeFamily) -> float | None:
    if value is None:
        return None
    if family in _NUMERIC:
        return float(value)
    if family in _TIME:  # normalized timestamps are UTC or naive; either way, compare as UTC
        return _ts(value).replace(tzinfo=dt.timezone.utc).timestamp()
    if family is TypeFamily.DATE:
        return float(dt.date.fromisoformat(value).toordinal())
    return None


def _concentration(
    scope: str,
    noun: str,
    names: Sequence[str],
    families: Sequence[TypeFamily],
    n_keys: int,
    affected: list[Row],
    unchanged: Sequence[Row],
) -> list[Diagnosis]:
    found: list[Diagnosis] = []
    n = len(affected)

    # a contiguous range of the key, or a narrow window of an ordered column
    ranged = False
    for i in range(len(names)):
        if families[i] not in _ORDERED or (i < n_keys and n_keys > 1):
            continue
        values = [
            (v, row[i]) for row in affected if (v := _number(row[i], families[i])) is not None
        ]
        others = [v for row in unchanged if (v := _number(row[i], families[i])) is not None]
        if len(values) < n * 0.95 or len(others) < defaults.CONCENTRATION_MIN_ROWS:
            continue
        low, high = min(values), max(values)
        inside = sum(1 for v in others if low[0] <= v <= high[0])
        span = high[0] - low[0]
        whole = max(others) - min(others)
        if inside == 0 and (i < n_keys or (whole > 0 and span <= whole * 0.1)):
            what = "a contiguous range" if i < n_keys else "a narrow window"
            found.append(
                Diagnosis(
                    kind="range",
                    scope=scope,
                    column=names[i],
                    rows=n,
                    of_rows=n,
                    message=f"{noun} all have {names[i]} in {low[1]} .. {high[1]}"
                    f" ({what} with no unchanged rows)",
                )
            )
            ranged = True
            break  # the key range is enough; correlated columns add nothing

    # one value of a column that is otherwise spread out
    for i in range(n_keys, len(names)):
        if families[i] in _ORDERED and ranged:
            continue
        top = _most_common([row[i] for row in affected])
        if top is None:
            continue
        value, count = top
        base = sum(1 for row in unchanged if row[i] == value)
        share, base_share = count / n, base / len(unchanged) if unchanged else 1.0
        if share >= 0.9 and base_share <= 0.5 and len(unchanged) >= defaults.CONCENTRATION_MIN_ROWS:
            shown = "NULL" if value is None else repr(value)
            found.append(
                Diagnosis(
                    kind="concentrated",
                    scope=scope,
                    column=names[i],
                    rows=count,
                    of_rows=n,
                    message=f"{share:.0%} of {noun} have {names[i]} = {shown}"
                    f" (vs {base_share:.0%} of unchanged rows)",
                )
            )
    return found
