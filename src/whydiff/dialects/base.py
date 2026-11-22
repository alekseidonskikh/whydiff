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
        return Column(name=name, native_type=data_type, family=family, scale=declared)

    @abstractmethod
    def classify(self, data_type: str) -> TypeFamily: ...

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

    # -- shared builders ------------------------------------------------------

    def normalized_sql(self, column: Column, rules: NormalizationRules) -> str:
        return self.normalize_sql(self.quote_ident(column.name), column, rules)

    def token_sql(self, column: Column, rules: NormalizationRules) -> str:
        value = self.normalized_sql(column, rules)
        return f"coalesce({self.byte_length_text_sql(value)} || ':' || {value}, 'N')"

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
