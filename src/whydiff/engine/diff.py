"""Compare two tables that may live in different databases.

1. Schema: match columns by name (case-insensitively), harmonize types.
2. Count rows on both sides.
3. Hash every row into one of N buckets by its key and compute, per bucket,
   ``count(*)`` and ``sum(row_hash)`` inside each database. Only these
   checksums leave the database.
4. Bisect: split each differing bucket that is still large into ``factor``
   children (``key_hash % (N * factor)``) and compare their checksums, level
   by level, until segments are small. Each level is one more scan per side,
   so it runs only when it saves downloading more than ``bisection_threshold``
   rows.
5. Download normalized rows only from the differing leaf segments and compare
   them by key: removed, added, changed (with the changed columns).
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial
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
HASH_SPACE = 1 << 60  # key hashes are 60-bit, so moduli beyond this split nothing
IDS_PER_QUERY = 10_000
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


@dataclass(frozen=True)
class _Side:
    """One table to compare, with its columns resolved and harmonized."""

    conn: Connector
    table: TableRef
    keys: list[Column]
    columns: list[Column]  # keys + compared values, for checksums
    values: list[Column]  # compared values only, for downloaded rows
    where: str | None

    def checksums(
        self, rules: NormalizationRules, modulus: int, parent: tuple[int, Sequence[int]] | None
    ) -> dict[int, tuple[int, int]]:
        d = self.conn.dialect
        result: dict[int, tuple[int, int]] = {}
        parents: Iterable[tuple[int, Sequence[int]] | None] = (
            [None]
            if parent is None
            else [(parent[0], ids) for ids in _chunks(parent[1], IDS_PER_QUERY)]
        )
        for p in parents:
            sql = d.segment_checksums_query(
                self.table, self.keys, self.columns, rules, modulus, self.where, p
            )
            result.update({int(b): (int(n), int(h)) for b, n, h in self.conn.execute(sql)})
        return result

    def rows(self, rules: NormalizationRules, segments: Sequence[_Segment]) -> list[Row]:
        d = self.conn.dialect
        by_modulus: dict[int, list[int]] = {}
        for seg in segments:
            by_modulus.setdefault(seg.modulus, []).append(seg.id)
        rows: list[Row] = []
        for modulus, ids in sorted(by_modulus.items()):
            for chunk in _chunks(ids, IDS_PER_QUERY):
                sql = d.segment_rows_query(
                    self.table, self.keys, self.values, rules, modulus, chunk, self.where
                )
                rows.extend(
                    tuple(None if v is None else str(v) for v in r) for r in self.conn.execute(sql)
                )
        return rows


@dataclass(frozen=True)
class _Segment:
    """Rows whose ``key_hash % modulus == id``; ``size`` is the larger side's count."""

    modulus: int
    id: int
    size: int


def _differing(
    modulus: int, a: dict[int, tuple[int, int]], b: dict[int, tuple[int, int]]
) -> list[_Segment]:
    empty = (0, 0)
    return [
        _Segment(modulus, s, max(a.get(s, empty)[0], b.get(s, empty)[0]))
        for s in sorted(a.keys() | b.keys())
        if a.get(s, empty) != b.get(s, empty)
    ]


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
    leaf_size: int = 64,
    bisection_factor: int = 32,
    bisection_threshold: int = 10_000,
    max_bisection_levels: int = 4,
    max_rows: int = 1_000_000,
    sample_limit: int = 10,
) -> DiffResult:
    """Diff two tables. ``where`` applies to both sides unless ``target_where`` is given.

    Bisection splits differing buckets larger than ``leaf_size`` rows into
    ``bisection_factor`` children per level, at most ``max_bisection_levels``
    levels, and only while the rows it would avoid downloading exceed
    ``bisection_threshold``. ``max_rows`` caps the rows downloaded per side.
    """
    started = time.perf_counter()
    rules = rules or NormalizationRules()
    src_ref = TableRef.parse(source_table) if isinstance(source_table, str) else source_table
    tgt_ref = TableRef.parse(target_table) if isinstance(target_table, str) else target_table
    tgt_where = target_where if target_where is not None else where
    if not key:
        raise ValueError("At least one key column is required")
    if bisection_factor < 2:
        raise ValueError("bisection_factor must be at least 2")
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
    compared = [*keys, *values]
    src = _Side(
        source,
        src_ref,
        [p.source for p in keys],
        [p.source for p in compared],
        [p.source for p in values],
        where,
    )
    tgt = _Side(
        target,
        tgt_ref,
        [p.target for p in keys],
        [p.target for p in compared],
        [p.target for p in values],
        tgt_where,
    )

    # 2. counts
    sd, td = source.dialect, target.dialect
    n_src, n_tgt = _both(
        parallel,
        lambda: int(source.execute(sd.count_query(src_ref, where))[0][0]),
        lambda: int(target.execute(td.count_query(tgt_ref, tgt_where))[0][0]),
    )
    buckets = max(1, min(MAX_BUCKETS, math.ceil(max(n_src, n_tgt) / bucket_size)))

    # 3. bucket checksums
    first = _differing(
        buckets,
        *_both(
            parallel,
            lambda: src.checksums(rules, buckets, None),
            lambda: tgt.checksums(rules, buckets, None),
        ),
    )

    # 4. bisection
    leaves: list[_Segment] = []
    current, modulus, levels = first, buckets, 0
    while current:
        large = [seg for seg in current if seg.size > leaf_size]
        leaves.extend(seg for seg in current if seg.size <= leaf_size)
        if (
            not large
            or levels >= max_bisection_levels
            or modulus * bisection_factor > HASH_SPACE
            or sum(seg.size for seg in large) <= bisection_threshold
        ):
            leaves.extend(large)
            break
        parent = (modulus, [seg.id for seg in large])
        modulus *= bisection_factor
        current = _differing(
            modulus,
            *_both(
                parallel,
                partial(src.checksums, rules, modulus, parent),
                partial(tgt.checksums, rules, modulus, parent),
            ),
        )
        levels += 1

    # 5. download within the row budget, then compare row by row
    selected: list[_Segment] = []
    budget = 0
    for seg in leaves:
        if selected and budget + seg.size > max_rows:
            break
        selected.append(seg)
        budget += seg.size
    rows_src, rows_tgt = _both(
        parallel, lambda: src.rows(rules, selected), lambda: tgt.rows(rules, selected)
    )

    result = DiffResult(
        status=Status.IDENTICAL,
        source=f"{sd.name}:{src_ref}",
        target=f"{td.name}:{tgt_ref}",
        key=[p.name for p in keys],
        where=where,
        rules=rules,
        schema_diff=schema,
        counts=RowCounts(source=n_src, target=n_tgt),
        buckets=BucketStats(
            total=buckets,
            differing=len(first),
            bisection_levels=levels,
            segments=len(leaves),
            inspected=len(selected),
            rows_downloaded=len(rows_src) + len(rows_tgt),
        ),
        truncated=len(selected) < len(leaves),
    )
    _compare_rows(result, rows_src, rows_tgt, len(keys), [p.name for p in compared], sample_limit)
    if first or n_src != n_tgt or schema.differs:
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
