"""DuckDB (also used for Parquet and CSV files)."""

from __future__ import annotations

import re

from whydiff.constants import FLOAT32_PRECISION, FLOAT64_PRECISION
from whydiff.dialects.base import Dialect
from whydiff.model import Column, TypeFamily
from whydiff.normalize.rules import NormalizationRules

_INTEGER = {
    "tinyint", "smallint", "integer", "bigint", "hugeint",
    "utinyint", "usmallint", "uinteger", "ubigint", "uhugeint",
}  # fmt: skip
_FLOAT = {"float", "real", "double"}
_TEXT = {"varchar", "text", "string"}
_SQL_TYPES = {
    "text": "varchar", "int": "integer", "bigint": "bigint", "float4": "float", "float8": "double",
}  # fmt: skip


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

    def float_precision(self, data_type: str) -> int:
        single = data_type.lower() in ("float", "real", "float4")
        return FLOAT32_PRECISION if single else FLOAT64_PRECISION

    def cast_sql(self, expr: str, sql_type: str) -> str:
        return f"cast({expr} as {_SQL_TYPES[sql_type]})"

    def float_special_sql(self, expr: str) -> tuple[str, str, str]:
        return (
            f"isnan({expr})",
            f"(isinf({expr}) and {expr} > 0)",
            f"(isinf({expr}) and {expr} < 0)",
        )

    def decimal_text_sql(self, expr: str, scale: int | None) -> str:
        return (
            f"cast({expr} as varchar)"
            if scale is None
            else f"cast(round({expr}, {scale}) as varchar)"
        )

    def _timestamp(self, expr: str, precision: int) -> str:
        width = self.timestamp_width(precision)
        return f"left(strftime({expr}, '%Y-%m-%dT%H:%M:%S.%f'), {width})"

    def normalize_sql(self, expr: str, column: Column, rules: NormalizationRules) -> str:
        family = column.family
        if family is TypeFamily.INTEGER:
            return f"cast({expr} as varchar)"
        if family in (TypeFamily.DECIMAL, TypeFamily.FLOAT):
            return self.number_sql(expr, column, rules)
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
