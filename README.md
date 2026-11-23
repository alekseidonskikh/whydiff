# whydiff

**Diff tables across databases — and find out *why* they differ.**

> [!WARNING]
> **Work in progress — not ready for use yet.** The core diff already runs on
> Postgres/Greenplum and DuckDB, but there is no release: the package on PyPI is
> only a name placeholder, and the CLI, API and output will change without notice.
> Please don't rely on it in production. Star or watch the repository to follow
> progress; issues and ideas are welcome.

Most diff tools tell you *whether* two tables match. whydiff tells you *what*
changed, and (soon) *why*:

```
$ whydiff diff postgresql://localhost/shop orders postgresql://localhost/shop orders_v2 --key id

rows     200,000 vs 199,999  (-1)
buckets  63 of 100 differ
bisect   1 level -> 101 differing segments
fetched  12,913 rows

removed  2   (in source only)
added    1   (in target only)
changed  101
         created_at: 100 rows
         amount: 1 rows

examples
  - id='17'
  + id='300001'
  ~ id='42'  amount: '2.94' -> '2.95'
  ~ id='5000'  created_at: '2024-01-04T11:20:00.000000' -> '2024-01-04T14:20:00.000000'

DIFFERENT in 2.65s
```

Coming in 0.3: `diagnosis  created_at shifted by exactly +03:00:00 → likely a timezone mismatch`.

## Try it

Until the first release, install from source:

```bash
pip install "whydiff[postgres,duckdb] @ git+https://github.com/alekseidonskikh/whydiff"

whydiff diff postgresql://user@prod/db public.orders duckdb:///dev.duckdb main.orders --key id
whydiff diff SRC TABLE TGT TABLE -k id --exclude updated_at --where "created_at >= '2024-01-01'"
whydiff diff SRC TABLE TGT TABLE -k id --timestamp-precision 3 --trim --json
whydiff diff SRC TABLE TGT TABLE -k id -v     # progress on stderr; -vv also logs every SQL query
```

Exit codes: `0` identical, `1` different, `2` error — ready for CI. The report
goes to stdout, logs and errors to stderr, and passwords in connection strings
are hidden in both.

From Python:

```python
from whydiff import connect, diff_tables, render_text

result = diff_tables(connect("postgresql://..."), "orders", connect("duckdb:///dev.duckdb"), "orders", ["id"])
print(render_text(result))
```

## How it works

1. Columns are matched by name; types from different databases are harmonized
   (an `integer` and a `numeric(12,2)` are both compared as decimals).
2. Every value is normalized to a canonical string (configurable: timestamp
   precision, decimal scale, float digits, trimming, case), each row is hashed
   to a 60-bit integer, and rows are spread over buckets by the hash of their key.
3. Each database returns only `count(*)` and `sum(row_hash)` per bucket. The
   sum does not depend on row order, so no sorting is needed.
4. Differing buckets that are still large are split into 32 sub-buckets and
   compared again (bisection), until segments hold about 64 rows. Each level
   costs one more scan, so it runs only when it saves downloading over 10,000
   rows. On the demo above it cuts the download from 252,547 rows to 12,913.
5. Only rows from the differing segments are downloaded and compared.

The SQL each database runs is tested against a pure-Python reference
implementation, so the same data hashes identically everywhere.

## Planned features

- **Any database pair** — Postgres, Greenplum, DuckDB, Parquet/CSV first;
  ClickHouse and Snowflake next; MySQL, Oracle, MSSQL later. SQL is generated
  once with [sqlglot](https://github.com/tobymao/sqlglot) and transpiled to
  each dialect.
- **Fast on large tables** — order-independent checksums per segment, so only
  the segments that differ are downloaded and compared (in DuckDB, via Arrow).
- **Explicit type normalization** — timestamp precision and time zones,
  decimal scale, float tolerance, `''` vs `NULL`, trailing spaces. Every rule
  is configurable and shown in the report.
- **Root-cause diagnosis** — time zone shifts, rounding, trimming, truncation,
  and differences concentrated in one slice of the data.
- **Real-world modes** — incremental windows, SCD2 tables, tables without a
  primary key, CDC/replication checks.
- **CI-friendly** — JSON output, exit codes, GitHub Action.

## Status and roadmap

| Version | Focus |
|---------|-------|
| 0.1 | Core engine: Postgres/Greenplum, DuckDB, schema diff, row diff, CLI |
| 0.2 | ClickHouse, Snowflake, SCD2, tolerances, HTML/Markdown reports |
| 0.3 | Root-cause diagnosis, `watch` mode for CDC, tables without a key |
| 1.0 | Stable Python API, docs site, connector plugins |

## License

MIT

## Development

Python runs on your machine through [uv](https://docs.astral.sh/uv/); the
databases run in Docker.

```bash
make setup   # .venv with dev dependencies, .env from .env.example
make up      # Postgres 16 on localhost:5433, with demo data
make test    # all tests, including Postgres
make demo    # diff the demo tables orders and orders_v2
make lint    # ruff + mypy, as in CI
make         # list all targets
```

Without make: `uv sync --group dev`, `docker compose up -d --wait`, then
`uv run pytest` with `WHYDIFF_TEST_PG_URI` set as in `.env.example`.
`make reset` wipes the database volume and re-runs the init scripts.
