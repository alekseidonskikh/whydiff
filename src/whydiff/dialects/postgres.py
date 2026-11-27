"""Postgres and Greenplum."""

from __future__ import annotations

from whydiff.constants import FLOAT32_PRECISION, FLOAT64_PRECISION
from whydiff.dialects.base import Dialect
from whydiff.model import Column, TypeFamily
from whydiff.normalize.rules import NormalizationRules

_INTEGER = {"smallint", "integer", "bigint", "int2", "int4", "int8"}
_FLOAT = {"real", "double precision", "float4", "float8"}
_TEXT = {"text", "character varying", "varchar", "character", "char", "bpchar", "name"}
_SQL_TYPES = {
    "text": "text", "int": "int", "bigint": "bigint", "float4": "float4", "float8": "float8",
}  # fmt: skip

#: lower() under the C locale changes ASCII letters only; an ICU collation
#: lowercases all of Unicode, like Python and DuckDB.
ICU_LOWER = 'lower(({}) collate "und-x-icu")'


class PostgresDialect(Dialect):
    name = "postgres"
    default_schema = "public"

    def __init__(self) -> None:
        # The connector checks the database and may switch to ICU_LOWER, or set
        # None when no Unicode-aware lower() is available.
        self.lower_template: str | None = "lower({})"

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

    def float_precision(self, data_type: str) -> int:
        single = data_type.lower() in ("real", "float4")
        return FLOAT32_PRECISION if single else FLOAT64_PRECISION

    def cast_sql(self, expr: str, sql_type: str) -> str:
        return f"({expr})::{_SQL_TYPES[sql_type]}"

    def float_special_sql(self, expr: str) -> tuple[str, str, str]:
        # Postgres treats NaN as equal to itself, so a plain comparison works
        return (
            f"{expr} = 'NaN'::float8",
            f"{expr} = 'Infinity'::float8",
            f"{expr} = '-Infinity'::float8",
        )

    def decimal_text_sql(self, expr: str, scale: int | None) -> str:
        number = f"({expr})::numeric"
        return f"{number}::text" if scale is None else f"round({number}, {scale})::text"

    def lower_sql(self, expr: str) -> str:
        if self.lower_template is None:
            raise ValueError(
                "--ignore-case needs a Unicode-aware lower(): the database uses the C locale "
                "and has no ICU collation"
            )
        return self.lower_template.format(expr)

    def _timestamp(self, expr: str, precision: int) -> str:
        width = 19 if precision == 0 else 20 + precision
        return f"left(to_char({expr}, 'YYYY-MM-DD\"T\"HH24:MI:SS.US'), {width})"

    def normalize_sql(self, expr: str, column: Column, rules: NormalizationRules) -> str:
        family = column.family
        if family is TypeFamily.INTEGER:
            return f"({expr})::text"
        if family in (TypeFamily.DECIMAL, TypeFamily.FLOAT):
            return self.number_sql(expr, column, rules)
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
                text = self.lower_sql(text)
        return text

    # Text is hashed and measured as UTF-8 whatever the database encoding is, so a
    # WIN1251 database and a UTF8 one agree. In SQL_ASCII the conversion does
    # nothing; the connector warns about that.

    def byte_length_text_sql(self, expr: str) -> str:
        return f"octet_length(convert_to({expr}, 'UTF8'))::text"

    def hash60_sql(self, text_expr: str) -> str:
        return f"('x' || substr(md5(convert_to({text_expr}, 'UTF8')), 1, 15))::bit(60)::bigint"
