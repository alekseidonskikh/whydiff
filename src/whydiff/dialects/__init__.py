from whydiff.dialects.base import Dialect
from whydiff.dialects.duckdb import DuckDBDialect
from whydiff.dialects.postgres import PostgresDialect

__all__ = ["Dialect", "DuckDBDialect", "PostgresDialect"]
