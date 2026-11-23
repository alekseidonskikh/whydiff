"""Diff the demo tables from the Python API, with logging.

Needs the Docker database (`make up`). Run it with:

    uv run python examples/demo.py              # report on stdout, progress on stderr
    uv run python examples/demo.py --debug      # also every SQL query
    uv run python examples/demo.py 2> demo.log  # logs into a file, report on screen

It writes the full result to demo-result.json next to where you run it.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from whydiff import DiffResult, NormalizationRules, Status, connect, diff_tables, render_text
from whydiff.connectors import Connector

DEMO_URI = "postgresql://whydiff:whydiff@localhost:5433/whydiff"


def run(source: Connector, target: Connector) -> DiffResult:
    """The diff itself; kept apart from connecting so it is easy to reuse."""
    return diff_tables(
        source,
        "public.orders",
        target,
        "public.orders_v2",
        key=["id"],
        rules=NormalizationRules(timestamp_precision=6),
        sample_limit=5,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--debug", action="store_true", help="log every SQL query")
    parser.add_argument("--uri", default=os.environ.get("WHYDIFF_PG_URI", DEMO_URI))
    args = parser.parse_args()

    # An application decides how logs look and where they go; whydiff itself
    # only emits them under the "whydiff" logger.
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    conn = connect(args.uri)
    try:
        result = run(conn, conn)
    finally:
        conn.close()

    print(render_text(result))

    out = Path("demo-result.json")
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logging.getLogger("demo").info("full result written to %s", out.resolve())

    # Everything in the report is also available as data:
    changed_columns = ", ".join(c.column for c in result.column_changes) or "none"
    print(f"\nchanged columns: {changed_columns}")
    return 0 if result.status is Status.IDENTICAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
