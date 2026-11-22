# whydiff

**Diff tables across databases — and find out *why* they differ.**

> 🚧 Early development. This release only reserves the name; nothing works yet.
> Star or watch the repository to follow progress.

Most diff tools tell you *whether* two tables match. whydiff aims to tell you
*what* changed and *why*:

```
$ whydiff diff pg://prod:orders duckdb://dev.duckdb:orders --key id

  rows      1,204,331 vs 1,204,331
  changed   18,402 rows, all in column created_at
  diagnosis constant shift of +03:00:00  →  likely a timezone mismatch
```

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
