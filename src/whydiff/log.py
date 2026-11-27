"""Logging for whydiff.

The library only creates loggers under ``whydiff`` and never configures
handlers: an application that uses whydiff decides where logs go. The CLI
calls :func:`configure_logging`.

Levels:

* INFO  - one line per stage: connections, schema, counts, each pass, result.
* DEBUG - every SQL query with its side, duration and number of rows.

Logs go to stderr, so stdout stays clean for the report and ``--json``.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TextIO

from whydiff.constants import LOGGER_NAME, SQL_LOG_LIMIT

_URI_PASSWORD = re.compile(r"(?P<head>[a-z][a-z0-9+.-]*://[^:/@\s]*:)[^@\s]*(?P<tail>@)", re.I)
_DSN_PASSWORD = re.compile(r"(?P<head>\bpassword\s*=\s*)('[^']*'|\S+)", re.I)


def get_logger(name: str | None = None) -> logging.Logger:
    """The package logger, or a child of it (``whydiff.<name>``)."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)


def redact(text: str) -> str:
    """Hide passwords in connection URIs and ``password=...`` DSN parts."""
    text = _URI_PASSWORD.sub(r"\g<head>***\g<tail>", text)
    return _DSN_PASSWORD.sub(r"\g<head>***", text)


def shorten_sql(sql: str, limit: int = SQL_LOG_LIMIT) -> str:
    sql = " ".join(sql.split())
    if len(sql) <= limit:
        return sql
    return f"{sql[:limit]} ... [{len(sql) - limit:,} more characters]"


def configure_logging(verbosity: int = 0, *, stream: TextIO | None = None) -> None:
    """Send whydiff logs to stderr: -1 = errors only, 0 = warnings, 1 = INFO, 2+ = DEBUG."""
    level = (
        logging.ERROR
        if verbosity < 0
        else logging.WARNING
        if verbosity == 0
        else logging.INFO
        if verbosity == 1
        else logging.DEBUG
    )
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s", "%H:%M:%S")
    )
    logger = get_logger()
    logger.handlers[:] = [handler]
    logger.setLevel(level)
    logger.propagate = False
