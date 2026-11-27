import datetime as dt
from decimal import Decimal

import pytest

from whydiff.constants import NULL_TOKEN
from whydiff.model import Column, TableRef, TypeFamily
from whydiff.normalize import NormalizationRules, encode_token, hash_normalized, normalize_value

RULES = NormalizationRules()


def col(family: TypeFamily, scale: int | None = None) -> Column:
    return Column("c", "t", family, scale)


def test_null_and_empty_string_differ() -> None:
    assert hash_normalized([None]) != hash_normalized([""])
    assert hash_normalized(["N"]) != hash_normalized([None])


def test_null_token_never_collides_with_a_value() -> None:
    assert encode_token(None) == NULL_TOKEN
    assert encode_token(NULL_TOKEN) == f"{len(NULL_TOKEN)}:{NULL_TOKEN}"
    assert hash_normalized([NULL_TOKEN]) != hash_normalized([None])
    # a value token always starts with its length, so never with the NULL token
    assert not encode_token("anything").startswith(NULL_TOKEN[0])


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
        # exact by default: trailing zeros go, nothing is rounded
        (Decimal("1.50"), None, "1.5"),
        (Decimal("1.5000"), None, "1.5"),
        (Decimal("2.345"), None, "2.345"),
        (Decimal("-0.004"), None, "-0.004"),
        (Decimal("1.0000001"), None, "1.0000001"),
        (Decimal("10"), None, "10"),
        (Decimal("0.00"), None, "0"),
        (Decimal("1E+3"), None, "1000"),
        # --decimal-scale rounds half away from zero first
        (Decimal("2.345"), 2, "2.35"),
        (Decimal("-2.345"), 2, "-2.35"),
        (Decimal("-0.004"), 2, "0"),
        (Decimal("1.25"), 1, "1.3"),
        (Decimal("1.5"), 2, "1.5"),
    ],
)
def test_decimal(value: Decimal, scale: int | None, expected: str) -> None:
    rules = NormalizationRules(decimal_scale=scale)
    # the declared scale of the column no longer matters
    assert normalize_value(value, col(TypeFamily.DECIMAL, 2), rules) == expected
    assert normalize_value(value, col(TypeFamily.DECIMAL, None), rules) == expected


def test_decimal_special_values() -> None:
    assert normalize_value(Decimal("NaN"), col(TypeFamily.DECIMAL), RULES) == "NaN"
    assert normalize_value(Decimal("-Infinity"), col(TypeFamily.DECIMAL), RULES) == "-Infinity"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.1 + 0.2, "3.0000000000000004e-1"),
        (0.1, "1e-1"),
        (1.25, "1.25e0"),
        (-2.675, "-2.675e0"),
        (100.0, "1e2"),
        # rounding to six places made both of these 0.000000
        (1.2e-7, "1.2e-7"),
        (3.4e-7, "3.4e-7"),
        # and this one overflowed DECIMAL(38, 6)
        (1e33, "1e33"),
        (0.0, "0"),
        (-0.0, "0"),
        (float("nan"), "NaN"),
        (float("inf"), "Infinity"),
        (float("-inf"), "-Infinity"),
    ],
)
def test_float_is_exact_by_default(value: float, expected: str) -> None:
    assert normalize_value(value, col(TypeFamily.FLOAT), RULES) == expected


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        (0.1 + 0.2, 15, "3e-1"),
        (2.675, 3, "2.68e0"),
        (9.9999, 3, "1e1"),  # the carry moves the exponent
        (1.2e-7, 1, "1e-7"),
        (123456.0, 2, "1.2e5"),
    ],
)
def test_float_significant_digits(value: float, digits: int, expected: str) -> None:
    rules = NormalizationRules(float_digits=digits)
    assert normalize_value(value, col(TypeFamily.FLOAT), rules) == expected


def test_single_precision_floats() -> None:
    single = Column("c", "real", TypeFamily.FLOAT, precision=24)
    assert normalize_value(0.1, single, RULES) == "1e-1"
    assert normalize_value(16777217.0, single, RULES) == "1.6777216e7"
    assert normalize_value(3.4028235e38, single, RULES) == "3.4028235e38"
    assert normalize_value(1e39, single, RULES) == "Infinity"


def test_timestamp_precision_truncates() -> None:
    ts = dt.datetime(2026, 1, 2, 3, 4, 5, 987654)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMP), RULES) == "2026-01-02T03:04:05.987654"
    rules = NormalizationRules(timestamp_precision=3)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMP), rules) == "2026-01-02T03:04:05.987"
    rules = NormalizationRules(timestamp_precision=0)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMP), rules) == "2026-01-02T03:04:05"


def test_timestamptz_is_converted_to_utc() -> None:
    msk = dt.timezone(dt.timedelta(hours=3))
    ts = dt.datetime(2026, 1, 2, 3, 0, tzinfo=msk)
    assert normalize_value(ts, col(TypeFamily.TIMESTAMPTZ), RULES) == "2026-01-02T00:00:00.000000"


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
    assert TableRef.parse(" s . t ") == TableRef("s", "t")
    for bad in ("a.b.c", "", "s.", ".t", " . "):
        with pytest.raises(ValueError):
            TableRef.parse(bad)
