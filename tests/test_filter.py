"""The where filter must stay one expression inside its parentheses."""

from __future__ import annotations

import pytest

from whydiff.dialects.base import check_filter
from whydiff.dialects.duckdb import DuckDBDialect
from whydiff.dialects.postgres import PostgresDialect
from whydiff.model import Column, TypeFamily
from whydiff.normalize import NormalizationRules


@pytest.mark.parametrize(
    "where",
    [
        "created_at >= '2026-01-01'",
        "status in ('new', 'paid') and (amount > 0 or amount is null)",
        "name = 'it''s; fine -- really'",
        '"odd;name" = 1 and "say ""hi""" is not null',
        "name like 'a\\_b%'",  # a plain string: the backslash is just a character
        "note = E'it\\'s (ok); still a string'",
        "price = 1e3",  # e before a number is not a string prefix
    ],
)
def test_filters_that_stay_one_expression(where: str) -> None:
    check_filter(where)


@pytest.mark.parametrize(
    ("where", "error"),
    [
        ("true); delete from orders; select (true", "unbalanced"),
        ("true; delete from orders", "';'"),
        ("id = 1) or (1 = 1", "unbalanced"),
        ("(id = 1", "unbalanced"),
        ("id = 1 -- and the rest", "comments"),
        ("id = /* hi */ 1", "comments"),
        ("name = $$x$$", "dollar"),
        ("name = 'open", "unterminated"),
        ('"open = 1', "unterminated"),
        # in an E-string \' does not close it, so the ; below is outside
        ("note = E'\\'' ; delete from orders; select '", "';'"),
        # in a plain string '' is a quote, so the string runs to the end
        ("note = 'a\\'' ; x", "unterminated"),
    ],
)
def test_filters_that_could_escape_are_refused(where: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        check_filter(where)


@pytest.mark.parametrize("dialect", [PostgresDialect(), DuckDBDialect()])
def test_numbers_in_sql_are_ints(dialect: PostgresDialect | DuckDBDialect) -> None:
    # model_construct skips validation, so the dialect must not trust the values
    bad = NormalizationRules.model_construct(float_digits="1); select 1; --")
    column = Column("v", "double", TypeFamily.FLOAT)
    with pytest.raises(ValueError):
        dialect.normalized_sql(column, bad)
    bad = NormalizationRules.model_construct(timestamp_precision="0); select 1; --")
    with pytest.raises(ValueError):
        dialect.normalized_sql(Column("t", "timestamp", TypeFamily.TIMESTAMP), bad)
