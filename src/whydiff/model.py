"""Core value types shared by dialects, connectors and the engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TypeFamily(str, Enum):
    """Database-independent group of column types.

    Normalization rules are defined per family, so a ``bigint`` in Postgres and
    a ``BIGINT`` in DuckDB hash to the same value.
    """

    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    TIMESTAMPTZ = "timestamptz"
    OTHER = "other"


@dataclass(frozen=True)
class Column:
    name: str
    native_type: str
    family: TypeFamily
    scale: int | None = None  # declared scale for DECIMAL columns, if known


@dataclass(frozen=True)
class TableRef:
    schema: str | None
    name: str

    @classmethod
    def parse(cls, value: str) -> TableRef:
        """Parse ``table`` or ``schema.table``; surrounding spaces are ignored."""
        parts = [part.strip() for part in value.split(".")]
        if len(parts) in (1, 2) and all(parts):
            return cls(schema=None, name=parts[0]) if len(parts) == 1 else cls(*parts)
        raise ValueError(f"Expected 'table' or 'schema.table', got {value!r}")

    def __str__(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name
