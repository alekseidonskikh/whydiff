"""SQL generation shared by all dialects.

A dialect knows three things about its database: how to name a table, how to
render each type family as a canonical string, and how to turn an md5 hex
digest into a 60-bit integer. Everything else (tokens, row hashes, bucket
checksums, drill-down queries) is built here once, so every database computes
exactly what :mod:`whydiff.normalize.reference` computes.

Raw ``where`` filters are passed through as written in the target dialect.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from whydiff.constants import FLOAT32_PRECISION, FLOAT64_PRECISION, LENGTH_SEPARATOR, NULL_TOKEN
from whydiff.model import Column, TableRef, TypeFamily
from whydiff.normalize.rules import NormalizationRules


class Dialect(ABC):
    name: str
    default_schema: str

    # -- identifiers and literals -------------------------------------------

    def quote_ident(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def literal(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def table_sql(self, table: TableRef) -> str:
        schema = table.schema or self.default_schema
        return f"{self.quote_ident(schema)}.{self.quote_ident(table.name)}"

    # -- introspection ------------------------------------------------------

    def columns_query(self, table: TableRef) -> str:
        schema = table.schema or self.default_schema
        return (
            "select column_name, data_type, numeric_scale "
            "from information_schema.columns "
            f"where table_schema = {self.literal(schema)} "
            f"and table_name = {self.literal(table.name)} "
            "order by ordinal_position"
        )

    def column_from_row(self, name: str, data_type: str, scale: object) -> Column:
        family = self.classify(data_type)
        declared = int(str(scale)) if scale is not None and family is TypeFamily.DECIMAL else None
        precision = self.float_precision(data_type) if family is TypeFamily.FLOAT else None
        return Column(
            name=name, native_type=data_type, family=family, scale=declared, precision=precision
        )

    @abstractmethod
    def classify(self, data_type: str) -> TypeFamily: ...

    @abstractmethod
    def float_precision(self, data_type: str) -> int:
        """24 for single-precision float types, 53 for double."""

    # -- dialect primitives ---------------------------------------------------

    @abstractmethod
    def normalize_sql(self, expr: str, column: Column, rules: NormalizationRules) -> str:
        """SQL text expression with the canonical string of a non-NULL value."""

    @abstractmethod
    def byte_length_text_sql(self, expr: str) -> str:
        """Byte length of a text expression, rendered as text."""

    @abstractmethod
    def hash60_sql(self, text_expr: str) -> str:
        """First 15 hex digits of md5(text) as a BIGINT."""

    @abstractmethod
    def cast_sql(self, expr: str, sql_type: str) -> str:
        """``expr`` cast to ``sql_type``, one of: text, int, bigint, float4, float8."""

    @abstractmethod
    def float_special_sql(self, expr: str) -> tuple[str, str, str]:
        """Conditions that a float is NaN, +Infinity and -Infinity."""

    # -- numbers ----------------------------------------------------------------
    #
    # Decimals and floats are spelled the same way in every database only after
    # some string work: the reference is canonical_decimal and canonical_float
    # in whydiff.normalize.reference, and these builders mirror them step by step.

    def decimal_sql(self, value_text: str) -> str:
        """Drop trailing zeros (and a bare point) from a plain decimal text."""
        pattern = self.literal(r"\.0*$|(\.[0-9]*[1-9])0+$")
        keep = self.literal(r"\1")
        return f"regexp_replace({value_text}, {pattern}, {keep})"

    def float_sql(self, expr: str, precision: int, significant: int | None) -> str:
        """Scientific notation of the shortest round-trip decimal, as canonical_float."""
        sql_type = "float4" if precision == FLOAT32_PRECISION else "float8"
        f = self.cast_sql(expr, sql_type)
        nan, pos_inf, neg_inf = self.float_special_sql(f)
        # the database prints the shortest decimal that reads back as the same float
        lowered = f"lower({self.cast_sql(f'abs({f})', 'text')})"
        mantissa = f"split_part({lowered}, 'e', 1)"
        exp_text = f"coalesce(nullif(split_part({lowered}, 'e', 2), ''), '0')"
        whole = f"split_part({mantissa}, '.', 1)"
        frac = f"split_part({mantissa}, '.', 2)"
        raw = f"({whole} || {frac})"
        no_lead = f"ltrim({raw}, '0')"
        digits = f"rtrim({no_lead}, '0')"
        exponent = (
            f"({self.cast_sql(exp_text, 'int')} + length({whole}) - 1"
            f" - (length({raw}) - length({no_lead})))"
        )
        if significant is not None:
            head = f"left({digits}, {significant})"
            bump = f"(case when substr({digits}, {significant + 1}, 1) >= '5' then 1 else 0 end)"
            rounded = self.cast_sql(f"{self.cast_sql(head, 'bigint')} + {bump}", "text")
            exponent = (
                f"({exponent} + (case when length({rounded}) > length({head}) then 1 else 0 end))"
            )
            digits = f"rtrim({rounded}, '0')"
        mantissa_out = (
            f"(left({digits}, 1) || (case when length({digits}) > 1"
            f" then '.' || substr({digits}, 2) else '' end))"
        )
        finite = (
            f"((case when {f} < 0 then '-' else '' end) || {mantissa_out}"
            f" || 'e' || {self.cast_sql(exponent, 'text')})"
        )
        return (
            f"(case when {nan} then 'NaN' when {pos_inf} then 'Infinity'"
            f" when {neg_inf} then '-Infinity' when {f} = 0 then '0' else {finite} end)"
        )

    def number_sql(self, expr: str, column: Column, rules: NormalizationRules) -> str:
        """Canonical text for DECIMAL and FLOAT columns."""
        if column.family is TypeFamily.FLOAT:
            precision = column.precision or FLOAT64_PRECISION
            return self.float_sql(expr, precision, rules.float_digits)
        return self.decimal_sql(self.decimal_text_sql(expr, rules.decimal_scale))

    @abstractmethod
    def decimal_text_sql(self, expr: str, scale: int | None) -> str:
        """Plain decimal text of a number, rounded half-up to ``scale`` if given."""

    # -- shared builders ------------------------------------------------------

    def normalized_sql(self, column: Column, rules: NormalizationRules) -> str:
        return self.normalize_sql(self.quote_ident(column.name), column, rules)

    def token_sql(self, column: Column, rules: NormalizationRules) -> str:
        value = self.normalized_sql(column, rules)
        length = self.byte_length_text_sql(value)
        separator, null = self.literal(LENGTH_SEPARATOR), self.literal(NULL_TOKEN)
        return f"coalesce({length} || {separator} || {value}, {null})"

    def row_hash_sql(self, columns: Sequence[Column], rules: NormalizationRules) -> str:
        if not columns:
            raise ValueError("At least one column is required to build a row hash")
        row = " || ".join(self.token_sql(c, rules) for c in columns)
        return self.hash60_sql(row)

    def key_hash_sql(self, keys: Sequence[Column], rules: NormalizationRules) -> str:
        """60-bit hash of the key columns; ``key_hash % modulus`` picks the segment."""
        return self.row_hash_sql(keys, rules)

    @staticmethod
    def _where(where: str | None, extra: str | None = None) -> str:
        parts = [f"({p})" for p in (where, extra) if p]
        return f" where {' and '.join(parts)}" if parts else ""

    @staticmethod
    def _in_list(expr: str, ids: Sequence[int]) -> str:
        return f"{expr} in ({', '.join(str(int(i)) for i in ids)})"

    def count_query(self, table: TableRef, where: str | None) -> str:
        return f"select count(*) from {self.table_sql(table)}{self._where(where)}"

    def segment_checksums_query(
        self,
        table: TableRef,
        keys: Sequence[Column],
        columns: Sequence[Column],
        rules: NormalizationRules,
        modulus: int,
        where: str | None,
        parent: tuple[int, Sequence[int]] | None = None,
    ) -> str:
        """One row per non-empty segment: (key_hash % modulus, row count, sum of row hashes).

        ``parent`` = (parent_modulus, parent_ids) restricts the scan to rows whose
        ``key_hash % parent_modulus`` is in ``parent_ids``; with ``modulus`` a
        multiple of ``parent_modulus`` this splits those parents into children.

        The sum does not depend on row order, so no sorting is needed and MPP
        databases can aggregate on every segment.
        """
        modulus = int(modulus)
        outer = ""
        if parent is not None:
            outer = " where " + self._in_list(f"kh % {int(parent[0])}", parent[1])
        return (
            f"select kh % {modulus} as b, count(*), sum(h) from ("
            f"select {self.key_hash_sql(keys, rules)} as kh, "
            f"{self.row_hash_sql(columns, rules)} as h "
            f"from {self.table_sql(table)}{self._where(where)}"
            f") t{outer} group by kh % {modulus}"
        )

    def segment_rows_query(
        self,
        table: TableRef,
        keys: Sequence[Column],
        columns: Sequence[Column],
        rules: NormalizationRules,
        modulus: int,
        segment_ids: Sequence[int],
        where: str | None,
    ) -> str:
        """Normalized key and column values for rows in the given segments."""
        selected = [self.normalized_sql(c, rules) for c in (*keys, *columns)]
        segment = self._in_list(f"({self.key_hash_sql(keys, rules)}) % {int(modulus)}", segment_ids)
        return (
            f"select {', '.join(selected)} "
            f"from {self.table_sql(table)}{self._where(where, segment)}"
        )
