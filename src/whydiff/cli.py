"""Command line: ``whydiff diff SOURCE TABLE TARGET TABLE --key id``.

Exit codes: 0 identical, 1 different, 2 error.
The report goes to stdout; logs (``-v``, ``-vv``) and errors go to stderr.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from whydiff import __version__, defaults
from whydiff.connectors import connect
from whydiff.engine import diff_tables
from whydiff.log import configure_logging, get_logger, redact
from whydiff.normalize import NormalizationRules
from whydiff.report import Status, render_text


def _split(values: Sequence[str] | None) -> list[str]:
    return [v.strip() for item in values or () for v in item.split(",") if v.strip()]


log = get_logger("cli")

#: Log verbosity without -v/-q: 0 warnings, 1 info, 2 debug.
VERBOSITY_ENV = "WHYDIFF_VERBOSITY"


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
    noise = d.add_mutually_exclusive_group()
    noise.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=None,
        help="log progress to stderr; -vv also logs every SQL query",
    )
    noise.add_argument("-q", "--quiet", action="store_true", help="log errors only")
    d.add_argument(
        "-k",
        "--key",
        action="append",
        required=True,
        help="key column(s), repeatable or comma-separated",
    )
    d.add_argument("-c", "--columns", action="append", help="compare only these columns")
    d.add_argument("-x", "--exclude", action="append", help="skip these columns")
    d.add_argument(
        "-w",
        "--where",
        help="SQL filter for both tables, one expression; it can read other tables, "
        "so only pass text you trust",
    )
    d.add_argument("--target-where", help="separate SQL filter for the target table")
    d.add_argument(
        "--timestamp-precision",
        type=int,
        default=defaults.TIMESTAMP_PRECISION,
        help="fractional second digits kept (default: %(default)s)",
    )
    d.add_argument(
        "--decimal-scale",
        type=int,
        help="round decimals to this many digits after the point (default: compare exactly)",
    )
    d.add_argument(
        "--float-digits",
        type=int,
        default=defaults.FLOAT_DIGITS,
        help="round floats to this many significant digits (default: compare exactly)",
    )
    d.add_argument("--trim", action="store_true", help="ignore leading and trailing spaces")
    d.add_argument("--ignore-case", action="store_true", help="compare strings case-insensitively")
    d.add_argument(
        "--bucket-size",
        type=int,
        default=defaults.BUCKET_SIZE,
        help="target rows per bucket (default: %(default)s)",
    )
    d.add_argument(
        "--leaf-size",
        type=int,
        default=defaults.LEAF_SIZE,
        help="stop bisecting segments at this many rows (default: %(default)s)",
    )
    d.add_argument(
        "--bisection-threshold",
        type=int,
        default=defaults.BISECTION_THRESHOLD,
        help="bisect only when it saves downloading more rows than this (default: %(default)s)",
    )
    d.add_argument("--no-bisect", action="store_true", help="download whole differing buckets")
    d.add_argument(
        "--max-rows",
        type=int,
        default=defaults.MAX_ROWS,
        help="rows downloaded per side at most (default: %(default)s)",
    )
    d.add_argument(
        "--samples",
        type=int,
        default=defaults.SAMPLE_LIMIT,
        help="examples shown per kind of difference (default: %(default)s)",
    )
    d.add_argument(
        "--statement-timeout",
        type=float,
        metavar="SECONDS",
        help="cancel any single query that runs longer than this (Postgres, Greenplum)",
    )
    d.add_argument(
        "--no-snapshot",
        action="store_true",
        help="do not hold one REPEATABLE READ transaction for the whole diff (Postgres)",
    )
    d.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def _verbosity(args: argparse.Namespace) -> int:
    if args.quiet:
        return -1
    if args.verbose is not None:
        return int(args.verbose)
    try:
        return int(os.environ.get(VERBOSITY_ENV, "0"))
    except ValueError:
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(_verbosity(args))
    try:
        rules = NormalizationRules(
            timestamp_precision=args.timestamp_precision,
            decimal_scale=args.decimal_scale,
            float_digits=args.float_digits,
            trim_strings=args.trim,
            case_insensitive=args.ignore_case,
        )
        options = {"statement_timeout": args.statement_timeout, "snapshot": not args.no_snapshot}
        source = connect(args.source, **options)
        target = source if args.target == args.source else connect(args.target, **options)
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
                leaf_size=args.leaf_size,
                bisection_threshold=args.bisection_threshold,
                max_bisection_levels=0 if args.no_bisect else defaults.MAX_BISECTION_LEVELS,
                max_rows=args.max_rows,
                sample_limit=args.samples,
            )
        finally:
            source.close()
            if target is not source:
                target.close()
    except Exception as exc:
        # drivers may echo the connection string, so passwords are hidden
        print(f"whydiff: error: {redact(str(exc))}", file=sys.stderr)
        log.debug("traceback", exc_info=True)
        return 2
    print(result.model_dump_json(indent=2) if args.json else render_text(result))
    return 0 if result.status is Status.IDENTICAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
