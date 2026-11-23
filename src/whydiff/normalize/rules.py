"""User-facing normalization settings.

Two databases rarely render the same value the same way: timestamps carry
different precision, decimals different scale, strings trailing spaces.
Every column is turned into a canonical string before hashing, and these
settings decide what "canonical" means. They are reported with every diff.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from whydiff import defaults
from whydiff.constants import DEFAULT_DECIMAL_DIGITS


class NormalizationRules(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp_precision: int = Field(
        default=defaults.TIMESTAMP_PRECISION,
        ge=0,
        le=6,
        description="Fractional second digits kept (truncated).",
    )
    decimal_scale: int | None = Field(
        default=None,
        ge=0,
        le=18,
        description="Digits after the point for decimals; None keeps the declared scale.",
    )
    float_digits: int = Field(
        default=defaults.FLOAT_DIGITS,
        ge=0,
        le=15,
        description="Digits after the point for floats (rounded).",
    )
    trim_strings: bool = Field(default=False, description="Strip leading and trailing spaces.")
    case_insensitive: bool = Field(default=False, description="Lowercase strings.")

    def decimal_digits(self, declared_scale: int | None) -> int:
        if self.decimal_scale is not None:
            return self.decimal_scale
        return declared_scale if declared_scale is not None else DEFAULT_DECIMAL_DIGITS
