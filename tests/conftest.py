from __future__ import annotations

import importlib.util
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from backends import DuckDBBackend, postgres_backend

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None


@pytest.fixture(params=["duckdb", "postgres"])
def backend(request: pytest.FixtureRequest) -> Iterator[Any]:
    """Every engine test runs once per available database."""
    conn: Any
    if request.param == "duckdb":
        if not HAS_DUCKDB:
            pytest.skip("duckdb is not installed")
        conn = DuckDBBackend()
    else:
        conn = postgres_backend()
        if conn is None:
            pytest.skip("set WHYDIFF_TEST_PG_URI or WHYDIFF_TEST_PG_PSQL to run Postgres tests")
    yield conn
    conn.close()


@pytest.fixture
def duckdb_backend() -> Iterator[Any]:
    if not HAS_DUCKDB:
        pytest.skip("duckdb is not installed")
    conn = DuckDBBackend()
    yield conn
    conn.close()


@pytest.fixture
def postgres() -> Iterator[Any]:
    conn = postgres_backend()
    if conn is None:
        pytest.skip("set WHYDIFF_TEST_PG_URI or WHYDIFF_TEST_PG_PSQL to run Postgres tests")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _restore_whydiff_logger() -> Iterator[None]:
    """The CLI configures the package logger; undo that after each test."""
    logger = logging.getLogger("whydiff")
    saved = (logger.handlers[:], logger.level, logger.propagate)
    yield
    logger.handlers[:], logger.level, logger.propagate = saved
