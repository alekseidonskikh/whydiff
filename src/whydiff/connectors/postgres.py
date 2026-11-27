from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from whydiff.dialects.postgres import ICU_LOWER, PostgresDialect
from whydiff.log import get_logger

log = get_logger("connect")

APPLICATION_NAME = "whydiff"

# Settings that change how Postgres prints values. The canonical forms avoid
# relying on them, but OTHER columns are compared as plain ::text, so a user's
# TimeZone or IntervalStyle must not leak into the hashes.
SESSION_SETTINGS = (
    ("TimeZone", "UTC"),
    ("DateStyle", "ISO, YMD"),
    ("IntervalStyle", "postgres"),
    ("bytea_output", "hex"),
    ("lc_numeric", "C"),
)

# From Postgres 12 any positive value prints the shortest text that reads back
# as the same float. Older servers (Greenplum 6 is 9.4) print 17 digits at 3.
SHORTEST_FLOAT_VERSION = 120000


class PostgresConnector:
    """Postgres and Greenplum through psycopg 3, read-only.

    With ``snapshot`` (the default) every query of the diff runs in one
    REPEATABLE READ READ ONLY transaction, so counts, checksums and the rows
    fetched later all see the same data even if the table changes meanwhile.
    The transaction stays open until ``close``; on a busy primary that holds
    back vacuum for the duration of the diff, which is what ``snapshot=False``
    is for.
    """

    def __init__(
        self, dsn: str, *, statement_timeout: float | None = None, snapshot: bool = True
    ) -> None:
        try:
            import psycopg
            from psycopg.conninfo import conninfo_to_dict
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "Postgres support needs psycopg: pip install 'whydiff[postgres]'"
            ) from exc
        self.dialect = PostgresDialect()
        # name the session so a DBA can tell these queries apart, unless the DSN does
        named = "application_name" in conninfo_to_dict(dsn)
        extra = {} if named else {"application_name": APPLICATION_NAME}
        self._conn = psycopg.connect(dsn, autocommit=not snapshot, **extra)
        try:
            if snapshot:
                self._conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                self._conn.read_only = True
            self._set_up(statement_timeout)
        except BaseException:
            self._conn.close()
            raise

    def _run(self, sql: str) -> Sequence[Sequence[Any]]:
        with self._conn.cursor() as cur:
            cur.execute(sql.encode(self._conn.info.encoding))
            rows: Sequence[Sequence[Any]] = cur.fetchall() if cur.description else []
            return rows

    def _set_up(self, statement_timeout: float | None) -> None:
        # In autocommit mode the transaction flag above does nothing, so the
        # session default is set as well. It guards against mistakes, not against
        # a crafted ``where``: raw SQL can change it back. For untrusted input,
        # connect with a read-only role.
        self._run("set default_transaction_read_only = on")
        for name, value in SESSION_SETTINGS:
            self._run(f"set {name} = '{value}'")
        version = self._conn.info.server_version
        if version >= SHORTEST_FLOAT_VERSION:
            self._run("set extra_float_digits = 1")
        else:
            self._run("set extra_float_digits = 3")
            log.warning(
                "server version %s prints floats with 17 digits, not the shortest form; "
                "floats may differ from other databases that print the shortest form",
                version,
            )
        if statement_timeout is not None:
            self._run(f"set statement_timeout = {max(1, round(statement_timeout * 1000))}")

        encoding = str(self._run("show server_encoding")[0][0])
        if encoding.upper() == "SQL_ASCII":
            log.warning(
                "server encoding is SQL_ASCII: text is hashed as stored bytes, so non-ASCII "
                "text matches only a database that stores the same bytes"
            )

        # Under the C locale lower() changes ASCII letters only. Python and DuckDB
        # lowercase all of Unicode, so --ignore-case would disagree on 'Я'. The
        # check spells the letters with chr() to keep the query itself ASCII, and
        # only makes sense in UTF8 databases.
        cyrillic = "lower(chr(1046) || chr(1071)) = chr(1078) || chr(1103)"
        if encoding.upper() == "UTF8" and not self._run(f"select {cyrillic}")[0][0]:
            icu = self._run("select count(*) from pg_collation where collname = 'und-x-icu'")
            self.dialect.lower_template = ICU_LOWER if icu[0][0] else None

    def execute(self, sql: str) -> Sequence[Sequence[Any]]:
        # psycopg only takes literal strings, bytes or psycopg.sql objects as queries,
        # to steer code away from SQL built with f-strings. Our queries are built
        # by the dialect, with identifiers quoted and literals escaped, so they
        # are passed as bytes in the connection's encoding.
        return self._run(sql)

    def close(self) -> None:
        # an open snapshot transaction is rolled back: nothing was written anyway
        self._conn.close()
