"""Postgres and Greenplum."""

from __future__ import annotations

from whydiff.dialects.base import Dialect
from whydiff.model import Column, TypeFamily
from whydiff.normalize.rules import NormalizationRules

_INTEGER = {"smallint", "integer", "bigint", "int2", "int4", "int8"}
_FLOAT = {"real", "double precision", "float4", "float8"}
_TEXT = {"text", "character varying", "varchar", "character", "char", "bpchar", "name"}


class PostgresDialect(Dialect):
    name = "postgres"
    default_schema = "public"

    def classify(self, data_type: str) -> TypeFamily:
        t = data_type.lower()
        if t in _INTEGER:
            return TypeFamily.INTEGER
        if t in ("numeric", "decimal"):
            return TypeFamily.DECIMAL
        if t in _FLOAT:
            return TypeFamily.FLOAT
        if t in _TEXT:
            return TypeFamily.TEXT
        if t == "boolean":
            return TypeFamily.BOOLEAN
        if t == "date":
            return TypeFamily.DATE
        if t == "timestamp without time zone":
            return TypeFamily.TIMESTAMP
        if t == "timestamp with time zone":
            return TypeFamily.TIMESTAMPTZ
        return TypeFamily.OTHER

    def _timestamp(self, expr: str, precision: int) -> str:
        width = 19 if precision == 0 else 20 + precision
        return f"left(to_char({expr}, 'YYYY-MM-DD\"T\"HH24:MI:SS.US'), {width})"

    def normalize_sql(self, expr: str, column: Column, rules: NormalizationRules) -> str:
        family = column.family
        if family is TypeFamily.INTEGER:
            return f"({expr})::text"
        if family is TypeFamily.DECIMAL:
            digits = rules.decimal_digits(column.scale)
            return f"round(({expr})::numeric, {digits})::text"
        if family is TypeFamily.FLOAT:
            return f"round(({expr})::numeric, {rules.float_digits})::text"
        if family is TypeFamily.BOOLEAN:
            return f"(case when {expr} then '1' when not {expr} then '0' end)"
        if family is TypeFamily.DATE:
            return f"to_char({expr}, 'YYYY-MM-DD')"
        if family is TypeFamily.TIMESTAMP:
            return self._timestamp(expr, rules.timestamp_precision)
        if family is TypeFamily.TIMESTAMPTZ:
            return self._timestamp(f"({expr}) at time zone 'UTC'", rules.timestamp_precision)
        text = f"({expr})::text"
        if family is TypeFamily.TEXT:
            if rules.trim_strings:
                text = f"btrim({text})"
            if rules.case_insensitive:
                text = f"lower({text})"
        return text

    def byte_length_text_sql(self, expr: str) -> str:
        return f"octet_length({expr})::text"

    def hash60_sql(self, text_expr: str) -> str:
        return f"('x' || substr(md5({text_expr}), 1, 15))::bit(60)::bigint"
