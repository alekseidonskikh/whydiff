"""Compare two tables that may live in different databases.

1. Schema: match columns by name (case-insensitively), harmonize types.
2. Count rows on both sides.
3. Hash every row into one of N buckets by its key and compute, per bucket,
   ``count(*)`` and ``sum(row_hash)`` inside each database. Only these
   checksums leave the database.
4. Download normalized rows only from buckets whose checksums differ and
   compare them by key: removed, added, changed (with the changed columns).
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from whydiff.connectors.base import Connector, get_columns
from whydiff.model import Column, TableRef, TypeFamily
from whydiff.normalize.rules import NormalizationRules
from whydiff.report.models import (
    BucketStats,
    ColumnChange,
    DiffResult,
    RowCounts,
    RowSample,
    SchemaDiff,
    Status,
    TypeMismatch,
)

MAX_BUCKETS = 1 << 18
IDS_PER_QUERY = 500
_NUMERIC_RANK = {TypeFamily.INTEGER: 0, TypeFamily.DECIMAL: 1, TypeFamily.FLOAT: 2}

T = TypeVar("T")
Row = tuple[str | None, ...]


@dataclass(frozen=True)
class _Pair:
    """The same logical column on both sides, with harmonized types."""

    name: str
    source: Column
    target: Column


def _harmonize(src: Column, tgt: Column) -> tuple[Column, Column, bool]:
    """Return columns normalized the same way, and whether the types differed."""
    if src.family is tgt.family:
        if src.family is TypeFamily.DECIMAL and src.scale != tgt.scale:
            scale = max(s for s in (src.scale, tgt.scale, 0) if s is not None)
            return replace(src, scale=scale), replace(tgt, scale=scale), True
        return src, tgt, False
    if src.family in _NUMERIC_RANK and tgt.family in _NUMERIC_RANK:
        wide = max(src.family, tgt.family, key=lambda f: _NUMERIC_RANK[f])
        scales = [s for s in (src.scale, tgt.scale) if s is not None]
        wide_scale = max(scales) if scales else None
        return (
            replace(src, family=wide, scale=wide_scale),
            replace(tgt, family=wide, scale=wide_scale),
            True,
        )
    return src, tgt, True


def _match_columns(
    source: list[Column], target: list[Column], wanted: Sequence[str] | None, exclude: set[str]
) -> tuple[list[_Pair], SchemaDiff]:
    target_by_name = {c.name.lower(): c for c in target}
    source_names = {c.name.lower() for c in source}
    pairs: list[_Pair] = []
    mismatches: list[TypeMismatch] = []
    wanted_lower = {w.lower() for w in wanted} if wanted else None
    for src in source:
        lname = src.name.lower()
        tgt = target_by_name.get(lname)
        if tgt is None or lname in exclude or (wanted_lower and lname not in wanted_lower):
            continue
        s, t, differed = _harmonize(src, tgt)
        if differed:
            mismatches.append(
                TypeMismatch(
                    column=src.name,
                    source_type=src.native_type,
                    target_type=tgt.native_type,
                    compared_as=(
                        s.family.value
                        if s.family is t.family
                        else f"{s.family.value} vs {t.family.value}"
                    ),
                )
            )
        pairs.append(_Pair(src.name, s, t))
    if wanted_lower:
        missing = wanted_lower - {p.name.lower() for p in pairs}
        if missing:
            raise LookupError(f"Columns not found in both tables: {sorted(missing)}")
    schema = SchemaDiff(
        compared_columns=[p.name for p in pairs],
        only_in_source=[c.name for c in source if c.name.lower() not in target_by_name],
        only_in_target=[c.name for c in target if c.name.lower() not in source_names],
        type_mismatches=mismatches,
    )
    return pairs, schema


def _both(parallel: bool, left: Any, right: Any) -> tuple[Any, Any]:
    if not parallel:
        return left(), right()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(left), pool.submit(right)
        return a.result(), b.result()


def _chunks(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _checksums(rows: Iterable[Sequence[Any]]) -> dict[int, tuple[int, int]]:
    return {int(b): (int(n), int(s)) for b, n, s in rows}


def diff_tables(
    source: Connector,
    source_table: str | TableRef,
    target: Connector,
    target_table: str | TableRef,
    key: Sequence[str],
    *,
    columns: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
    rules: NormalizationRules | None = None,
    where: str | None = None,
    target_where: str | None = None,
    bucket_size: int = 2000,
    max_rows: int = 1_000_000,
    sample_limit: int = 10,
) -> DiffResult:
    """Diff two tables. ``where`` applies to both sides unless ``target_where`` is given."""
    started = time.perf_counter()
    rules = rules or NormalizationRules()
    src_ref = TableRef.parse(source_table) if isinstance(source_table, str) else source_table
    tgt_ref = TableRef.parse(target_table) if isinstance(target_table, str) else target_table
    tgt_where = target_where if target_where is not None else where
    if not key:
        raise ValueError("At least one key column is required")
    parallel = source is not target

    # 1. schema
    src_cols, tgt_cols = _both(
        parallel, lambda: get_columns(source, src_ref), lambda: get_columns(target, tgt_ref)
    )
    key_lower = [k.lower() for k in key]
    excluded = {e.lower() for e in exclude}
    if excluded & set(key_lower):
        raise ValueError("Key columns cannot be excluded")
    all_pairs, schema = _match_columns(src_cols, tgt_cols, None, excluded)
    by_name = {p.name.lower(): p for p in all_pairs}
    missing_keys = [k for k in key if k.lower() not in by_name]
    if missing_keys:
        raise LookupError(f"Key columns not found in both tables: {missing_keys}")
    keys = [by_name[k] for k in key_lower]
    values, _ = _match_columns(src_cols, tgt_cols, columns, excluded | set(key_lower))
    if columns:
        schema.compared_columns = [p.name for p in values]
    key_names = [p.name for p in keys]
    compared = [*keys, *values]

    # 2. counts
    sd, td = source.dialect, target.dialect
    n_src, n_tgt = _both(
        parallel,
        lambda: int(source.execute(sd.count_query(src_ref, where))[0][0]),
        lambda: int(target.execute(td.count_query(tgt_ref, tgt_where))[0][0]),
    )
    buckets = max(1, min(MAX_BUCKETS, math.ceil(max(n_src, n_tgt) / bucket_size)))

    # 3. bucket checksums
    src_keys, tgt_keys = [p.source for p in keys], [p.target for p in keys]
    src_all, tgt_all = [p.source for p in compared], [p.target for p in compared]
    sums_src, sums_tgt = _both(
        parallel,
        lambda: _checksums(
            source.execute(
                sd.bucket_checksums_query(src_ref, src_keys, src_all, rules, buckets, where)
            )
        ),
        lambda: _checksums(
            target.execute(
                td.bucket_checksums_query(tgt_ref, tgt_keys, tgt_all, rules, buckets, tgt_where)
            )
        ),
    )
    empty = (0, 0)
    differing = sorted(
        b
        for b in sums_src.keys() | sums_tgt.keys()
        if sums_src.get(b, empty) != sums_tgt.get(b, empty)
    )

    # 4. drill down, within the row budget
    inspect: list[int] = []
    budget = 0
    for b in differing:
        size = max(sums_src.get(b, empty)[0], sums_tgt.get(b, empty)[0])
        if inspect and budget + size > max_rows:
            break
        inspect.append(b)
        budget += size

    def fetch(
        conn: Connector, ref: TableRef, k: list[Column], c: list[Column], w: str | None
    ) -> list[Row]:
        rows: list[Row] = []
        for ids in _chunks(inspect, IDS_PER_QUERY):
            sql = conn.dialect.bucket_rows_query(ref, k, c, rules, buckets, ids, w)
            rows.extend(tuple(None if v is None else str(v) for v in r) for r in conn.execute(sql))
        return rows

    rows_src, rows_tgt = _both(
        parallel,
        lambda: fetch(source, src_ref, src_keys, [p.source for p in values], where),
        lambda: fetch(target, tgt_ref, tgt_keys, [p.target for p in values], tgt_where),
    )

    result = DiffResult(
        status=Status.IDENTICAL,
        source=f"{sd.name}:{src_ref}",
        target=f"{td.name}:{tgt_ref}",
        key=key_names,
        where=where,
        rules=rules,
        schema_diff=schema,
        counts=RowCounts(source=n_src, target=n_tgt),
        buckets=BucketStats(total=buckets, differing=len(differing), inspected=len(inspect)),
        truncated=len(inspect) < len(differing),
    )
    _compare_rows(result, rows_src, rows_tgt, len(keys), [p.name for p in compared], sample_limit)
    data_differs = bool(differing) or n_src != n_tgt
    if data_differs or schema.differs:
        result.status = Status.DIFFERENT
    result.elapsed_seconds = round(time.perf_counter() - started, 3)
    return result


def _index(rows: list[Row], n_keys: int) -> tuple[dict[Row, Row], int]:
    index: dict[Row, Row] = {}
    seen: Counter[Row] = Counter()
    for row in rows:
        k = row[:n_keys]
        seen[k] += 1
        index.setdefault(k, row)
    return index, sum(1 for n in seen.values() if n > 1)


def _compare_rows(
    result: DiffResult,
    rows_src: list[Row],
    rows_tgt: list[Row],
    n_keys: int,
    names: list[str],
    sample_limit: int,
) -> None:
    src, result.duplicate_keys_source = _index(rows_src, n_keys)
    tgt, result.duplicate_keys_target = _index(rows_tgt, n_keys)
    per_column: Counter[str] = Counter()
    samples: dict[str, list[RowSample]] = {"removed": [], "added": [], "changed": []}

    def as_dict(row: Row) -> dict[str, str | None]:
        return dict(zip(names, row, strict=True))

    def keep(kind: str, k: Row, **fields: Any) -> None:
        if len(samples[kind]) < sample_limit:
            samples[kind].append(
                RowSample(kind=kind, key=dict(zip(names[:n_keys], k, strict=True)), **fields)
            )

    for k in sorted(src.keys() - tgt.keys(), key=repr):
        result.removed += 1
        keep("removed", k, source=as_dict(src[k]))
    for k in sorted(tgt.keys() - src.keys(), key=repr):
        result.added += 1
        keep("added", k, target=as_dict(tgt[k]))
    for k in sorted(src.keys() & tgt.keys(), key=repr):
        a, b = src[k], tgt[k]
        if a == b:
            continue
        changed = [names[i] for i in range(n_keys, len(names)) if a[i] != b[i]]
        result.changed += 1
        per_column.update(changed)
        keep("changed", k, source=as_dict(a), target=as_dict(b), changed_columns=changed)

    result.column_changes = [
        ColumnChange(column=c, rows_changed=n) for c, n in per_column.most_common()
    ]
    result.samples = [*samples["removed"], *samples["added"], *samples["changed"]]
