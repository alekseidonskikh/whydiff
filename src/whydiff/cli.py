"""Command line: ``whydiff diff SOURCE TABLE TARGET TABLE --key id``.

Exit codes: 0 identical, 1 different, 2 error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from whydiff import __version__
from whydiff.connectors import connect
from whydiff.engine import diff_tables
from whydiff.normalize import NormalizationRules
from whydiff.report import Status, render_text


def _split(values: Sequence[str] | None) -> list[str]:
    return [v.strip() for item in values or () for v in item.split(",") if v.strip()]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whydiff", description="Diff tables across databases and explain why they differ."
    )
    parser.add_argument("--version", action="version", version=f"whydiff {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("diff", help="compare two tables")
    d.add_argument("source", help="source database URI, e.g. postgresql://user@host/db")
    d.add_argument("source_table", help="table or schema.table")
    d.add_argument("target", help="target database URI, e.g. duckdb:///dev.duckdb")
    d.add_argument("target_table", help="table or schema.table")
    d.add_argument(
        "-k",
        "--key",
        action="append",
        required=True,
        help="key column(s), repeatable or comma-separated",
    )
    d.add_argument("-c", "--columns", action="append", help="compare only these columns")
    d.add_argument("-x", "--exclude", action="append", help="skip these columns")
    d.add_argument("-w", "--where", help="filter applied to both tables (SQL)")
    d.add_argument("--target-where", help="separate filter for the target table")
    d.add_argument(
        "--timestamp-precision", type=int, default=6, help="fractional second digits kept"
    )
    d.add_argument("--decimal-scale", type=int, help="digits after the point for decimals")
    d.add_argument("--float-digits", type=int, default=6, help="digits after the point for floats")
    d.add_argument("--trim", action="store_true", help="ignore leading and trailing spaces")
    d.add_argument("--ignore-case", action="store_true", help="compare strings case-insensitively")
    d.add_argument("--bucket-size", type=int, default=2000, help="target rows per bucket")
    d.add_argument("--max-rows", type=int, default=1_000_000, help="row budget for drill-down")
    d.add_argument("--samples", type=int, default=10, help="examples shown per kind of difference")
    d.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rules = NormalizationRules(
            timestamp_precision=args.timestamp_precision,
            decimal_scale=args.decimal_scale,
            float_digits=args.float_digits,
            trim_strings=args.trim,
            case_insensitive=args.ignore_case,
        )
        source = connect(args.source)
        target = source if args.target == args.source else connect(args.target)
        try:
            result = diff_tables(
                source,
                args.source_table,
                target,
                args.target_table,
                _split(args.key),
                columns=_split(args.columns) or None,
                exclude=_split(args.exclude),
                rules=rules,
                where=args.where,
                target_where=args.target_where,
                bucket_size=args.bucket_size,
                max_rows=args.max_rows,
                sample_limit=args.samples,
            )
        finally:
            source.close()
            if target is not source:
                target.close()
    except Exception as exc:
        print(f"whydiff: error: {exc}", file=sys.stderr)
        return 2
    print(result.model_dump_json(indent=2) if args.json else render_text(result))
    return 0 if result.status is Status.IDENTICAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
