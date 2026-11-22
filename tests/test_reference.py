import datetime as dt
from decimal import Decimal

import pytest

from whydiff.model import Column, TableRef, TypeFamily
from whydiff.normalize import NormalizationRules, encode_token, hash_normalized, normalize_value

RULES = NormalizationRules()


def col(family: TypeFamily, scale: int | None = None) -> Column:
    return Column("c", "t", family, scale)


def test_null_and_empty_string_differ() -> None:
    assert hash_normalized([None]) != hash_normalized([""])
    assert hash_normalized(["N"]) != hash_normalized([None])


def test_encoding_is_unambiguous_across_column_boundaries() -> None:
    assert hash_normalized(["a|", "b"]) != hash_normalized(["a", "|b"])
    assert hash_normalized(["ab", "c"]) != hash_normalized(["a", "bc"])
    assert hash_normalized(["1:a", None]) != hash_normalized(["1", ":aN"])


def test_token_uses_byte_length() -> None:
    assert encode_token("ж") == "2:ж"


def test_hash_fits_signed_bigint() -> None:
    h = hash_normalized(["anything"])
    assert 0 <= h < 2**60


@pytest.mark.parametrize(
    ("value", "scale", "expected"),
    [
        (Decimal("1.5"), 2, "1.50"),
        (Decimal("2.345"), 2, "2.35"),
        (Decimal("-2.345"), 2, "-2.35"),
        (Decimal("-0.004"), 2, "0.00"),
        (Decimal("10"), 0, "10"),
    ],
)
def test_decimal(value: Decimal, scale: int, expected: str) -> None:
    assert normalize_value(value, col(TypeFamily.DECIMAL, scale), RULES) == expected


def test_decimal_scale_override() -> None:
    rules = NormalizationRules(decimal_scale=1)
    assert normalize_value(Decimal("1.25"), col(TypeFamily.DECIMAL, 2), rules) == "1.3"


def test_float_rounds_to_configured_digits() -> None:
    assert normalize_value(0.1 + 0.2, col(TypeFamily.FLOAT), RULES) == "0.300000"
    assert normalize_value(float("nan"), col(TypeFamily.FLOAT), RULES) == "NaN"


def test_timestamp_precision_truncates() -> None:
    ts = dt.datetime(2024, 1, 2, 3, 4, 5, 987654)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMP), RULES) == "2024-01-02T03:04:05.987654"
    rules = NormalizationRules(timestamp_precision=3)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMP), rules) == "2024-01-02T03:04:05.987"
    rules = NormalizationRules(timestamp_precision=0)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMP), rules) == "2024-01-02T03:04:05"


def test_timestamptz_is_converted_to_utc() -> None:
    msk = dt.timezone(dt.timedelta(hours=3))
    ts = dt.datetime(2024, 1, 2, 3, 0, tzinfo=msk)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMPTZ), RULES) == "2024-01-02T00:00:00.000000"


def test_text_options() -> None:
    rules = NormalizationRules(trim_strings=True, case_insensitive=True)
    assert normalize_value("  Hello ", col(TypeFamily.TEXT), rules) == "hello"
    assert normalize_value("  Hello ", col(TypeFamily.TEXT), RULES) == "  Hello "


def test_boolean() -> None:
    assert normalize_value(True, col(TypeFamily.BOOLEAN), RULES) == "1"
    assert normalize_value(False, col(TypeFamily.BOOLEAN), RULES) == "0"


def test_table_ref() -> None:
    assert TableRef.parse("s.t") == TableRef("s", "t")
    assert TableRef.parse("t") == TableRef(None, "t")
    with pytest.raises(ValueError):
        TableRef.parse("a.b.c")
