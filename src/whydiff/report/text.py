"""Plain-text report for the terminal."""

from __future__ import annotations

from whydiff.report.models import DiffResult, RowSample, Status


def _fmt(value: str | None) -> str:
    return "NULL" if value is None else repr(value)


def _key(sample: RowSample) -> str:
    return ", ".join(f"{k}={_fmt(v)}" for k, v in sample.key.items())


def render_text(result: DiffResult) -> str:
    lines: list[str] = []
    add = lines.append
    add(f"source   {result.source}")
    add(f"target   {result.target}")
    add(f"key      {', '.join(result.key)}")
    if result.where:
        add(f"where    {result.where}")
    add("")

    s = result.schema_diff
    add(f"columns  {len(s.compared_columns)} compared")
    if s.only_in_source:
        add(f"         only in source: {', '.join(s.only_in_source)}")
    if s.only_in_target:
        add(f"         only in target: {', '.join(s.only_in_target)}")
    for m in s.type_mismatches:
        add(
            f"         {m.column}: {m.source_type} vs {m.target_type} (compared as {m.compared_as})"
        )

    c = result.counts
    delta = c.target - c.source
    add(f"rows     {c.source:,} vs {c.target:,}" + (f"  ({delta:+,})" if delta else ""))
    b = result.buckets
    add(f"buckets  {b.differing:,} of {b.total:,} differ")
    if b.bisection_levels:
        levels = "level" if b.bisection_levels == 1 else "levels"
        add(f"bisect   {b.bisection_levels} {levels} -> {b.segments:,} differing segments")
    if b.differing:
        add(f"fetched  {b.rows_downloaded:,} rows")
    if result.truncated:
        add(f"         row budget reached: inspected {b.inspected:,} of {b.segments:,} segments")

    if result.removed or result.added or result.changed:
        add("")
        add(f"removed  {result.removed:,}   (in source only)")
        add(f"added    {result.added:,}   (in target only)")
        add(f"changed  {result.changed:,}")
        for cc in result.column_changes:
            add(f"         {cc.column}: {cc.rows_changed:,} rows")
    for label, n in (
        ("source", result.duplicate_keys_source),
        ("target", result.duplicate_keys_target),
    ):
        if n:
            add(f"warning  {n:,} duplicate keys in {label}; results for them are approximate")

    if result.samples:
        add("")
        add("examples")
        for sm in result.samples:
            if sm.kind == "changed" and sm.source and sm.target:
                diffs = "; ".join(
                    f"{col}: {_fmt(sm.source[col])} -> {_fmt(sm.target[col])}"
                    for col in sm.changed_columns
                )
                add(f"  ~ {_key(sm)}  {diffs}")
            else:
                add(f"  {'-' if sm.kind == 'removed' else '+'} {_key(sm)}")

    add("")
    verdict = "IDENTICAL" if result.status is Status.IDENTICAL else "DIFFERENT"
    add(f"{verdict} in {result.elapsed_seconds:.2f}s")
    return "\n".join(lines)
