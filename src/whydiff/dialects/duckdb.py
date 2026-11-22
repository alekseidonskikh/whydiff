"""DuckDB (also used for Parquet and CSV files)."""

from __future__ import annotations

import re

from whydiff.dialects.base import Dialect
from whydiff.model import Column, TypeFamily
from whydiff.normalize.rules import NormalizationRules

_INTEGER = {
    "tinyint", "smallint", "integer", "bigint", "hugeint",
    "utinyint", "usmallint", "uinteger", "ubigint", "uhugeint",
}  # fmt: skip
_FLOAT = {"float", "real", "double"}
_TEXT = {"varchar", "text", "string"}


class DuckDBDialect(Dialect):
    name = "duckdb"
    default_schema = "main"

    def classify(self, data_type: str) -> TypeFamily:
        t = data_type.lower()
        if t in _INTEGER:
            return TypeFamily.INTEGER
        if t.startswith(("decimal", "numeric")):
            return TypeFamily.DECIMAL
        if t in _FLOAT:
            return TypeFamily.FLOAT
        if t in _TEXT or re.fullmatch(r"varchar\(\d+\)", t):
            return TypeFamily.TEXT
        if t == "boolean":
            return TypeFamily.BOOLEAN
        if t == "date":
            return TypeFamily.DATE
        if t in ("timestamp", "timestamp_ms", "timestamp_s", "timestamp_ns", "datetime"):
            return TypeFamily.TIMESTAMP
        if t in ("timestamp with time zone", "timestamptz"):
            return TypeFamily.TIMESTAMPTZ
        return TypeFamily.OTHER

    def _timestamp(self, expr: str, precision: int) -> str:
        width = 19 if precision == 0 else 20 + precision
        return f"left(strftime({expr}, '%Y-%m-%dT%H:%M:%S.%f'), {width})"

    def normalize_sql(self, expr: str, column: Column, rules: NormalizationRules) -> str:
        family = column.family
        if family is TypeFamily.INTEGER:
            return f"cast({expr} as varchar)"
        if family is TypeFamily.DECIMAL:
            digits = rules.decimal_digits(column.scale)
            return f"cast(cast({expr} as decimal(38, {digits})) as varchar)"
        if family is TypeFamily.FLOAT:
            return f"cast(cast({expr} as decimal(38, {rules.float_digits})) as varchar)"
        if family is TypeFamily.BOOLEAN:
            return f"(case when {expr} then '1' when not {expr} then '0' end)"
        if family is TypeFamily.DATE:
            return f"strftime({expr}, '%Y-%m-%d')"
        if family is TypeFamily.TIMESTAMP:
            return self._timestamp(expr, rules.timestamp_precision)
        if family is TypeFamily.TIMESTAMPTZ:
            return self._timestamp(f"timezone('UTC', {expr})", rules.timestamp_precision)
        text = f"cast({expr} as varchar)"
        if family is TypeFamily.TEXT:
            if rules.trim_strings:
                text = f"trim({text})"
            if rules.case_insensitive:
                text = f"lower({text})"
        return text

    def byte_length_text_sql(self, expr: str) -> str:
        return f"cast(strlen({expr}) as varchar)"

    def hash60_sql(self, text_expr: str) -> str:
        return f"cast('0x' || substr(md5({text_expr}), 1, 15) as bigint)"
