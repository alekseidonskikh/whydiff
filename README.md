# whydiff

Diff two tables, even in different databases, and get a guess at why they differ.

> [!WARNING]
> Work in progress, not usable yet. The diff itself works on Postgres/Greenplum
> and DuckDB, but nothing is released: the package on PyPI only holds the name,
> and the CLI, the API and the output will change. Please don't use it in
> production. Issues and ideas are welcome.

Most diff tools stop at "these tables don't match". whydiff also shows which
rows and columns changed and tries to explain the change. Here it compares an
orders table with a copy that went through a buggy pipeline:

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
         amount: 1 row

diagnosis
  created_at: shifted by exactly +03:00:00 -> likely a time zone mismatch  (100 of 100 changed)
  rows with a changed created_at all have id in 5000 .. 5099 (a contiguous range with no unchanged rows)  (100 of 100 changed)

examples
  - id='17'
  + id='300001'
  ~ id='42'  amount: '2.94' -> '2.95'
  ~ id='5000'  created_at: '2026-01-04T11:20:00.000000' -> '2026-01-04T14:20:00.000000'

DIFFERENT in 3.40s
```

The diagnosis is the main point of the project. For each changed column
whydiff looks at the pairs of old and new values and checks for common causes:
a constant time shift (a time zone if it is a whole number of hours), lost
timestamp precision, rounding, a unit change like x100, a constant offset,
float noise, trimmed, case-changed or truncated text, `''` vs `NULL`, values
set to `NULL` or filled in, flipped booleans, swapped columns. It also checks
whether the affected rows sit in one key range or time window, or share one
value of some column. A finding is shown only if it covers at least 95% of the
affected rows, and never for a single row.

## Try it

There is no release yet, so install from git:

```bash
pip install "whydiff[postgres,duckdb] @ git+https://github.com/alekseidonskikh/whydiff"

whydiff diff postgresql://user@prod/db public.orders duckdb:///dev.duckdb main.orders --key id
whydiff diff SRC TABLE TGT TABLE -k id --exclude updated_at --where "created_at >= '2026-01-01'"
whydiff diff SRC TABLE TGT TABLE -k id --timestamp-precision 3 --trim --json
whydiff diff SRC TABLE TGT TABLE -k id -v     # progress on stderr; -vv also logs every SQL query
```

The exit code is 0 if the tables are identical, 1 if they differ and 2 on an
error, so it works as a CI check. The report goes to stdout, logs and errors to
stderr. Passwords in connection strings are masked in both.

From Python:

```python
from whydiff import connect, diff_tables, render_text

result = diff_tables(connect("postgresql://..."), "orders", connect("duckdb:///dev.duckdb"), "orders", ["id"])
print(render_text(result))
```

## How it works

1. Columns are matched by name, ignoring case. Types are harmonized across
   databases, so an `integer` on one side and a `numeric(12,2)` on the other
   are both compared as decimals.
2. Every value is turned into a canonical string. Numbers are compared
   exactly by default: `1.50` equals `1.5`, but `1.2e-7` does not equal
   `3.4e-7`. Rounding (`--decimal-scale`, `--float-digits`), timestamp
   precision, trimming and case are options. Text is hashed as UTF-8 whatever
   the database encoding is. Each row is hashed into a 60-bit integer, and rows
   go into buckets by the hash of their key.
3. Each database returns only `count(*)` and `sum(row_hash)` per bucket. A sum
   doesn't depend on row order, so nothing has to be sorted.
4. A differing bucket that is still large is split into 32 parts and compared
   again, until segments are down to about 64 rows. Every level is one more
   scan of the table, so it only runs when it saves downloading more than
   10,000 rows. On the demo above this brings the download from 252,547 rows
   to 12,913.
5. Rows are downloaded only from the differing segments and compared by key.
6. The diagnosis runs on those rows. The unchanged rows from the same segments
   are a random sample of the table and serve as the baseline.

The SQL for each database is tested against a reference implementation in
plain Python, so the same data gets the same hash everywhere.

## Plans

- More sources: Parquet and CSV through DuckDB, then ClickHouse and Snowflake,
  later MySQL, Oracle and MSSQL.
- A same-database mode that runs a `FULL OUTER JOIN` in the database instead
  of downloading rows.
- Tolerances for floats and decimals.
- Incremental windows, SCD2 tables, tables without a primary key, replication
  and CDC checks.
- More diagnoses, Markdown and HTML reports, a GitHub Action.

| Version | Focus |
|---------|-------|
| 0.1 | Core engine: Postgres/Greenplum, DuckDB, schema diff, row diff, CLI |
| 0.2 | ClickHouse, Snowflake, SCD2, tolerances, HTML/Markdown reports |
| 0.3 | More diagnoses, `watch` mode for CDC, tables without a key |
| 1.0 | Stable Python API, docs site, connector plugins |

## Development

Python runs on your machine through [uv](https://docs.astral.sh/uv/), the
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

## License

MIT
