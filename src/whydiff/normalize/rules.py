"""User-facing normalization settings.

Two databases rarely render the same value the same way: timestamps carry
different precision, decimals different scale, strings trailing spaces.
Every column is turned into a canonical string before hashing, and these
settings decide what "canonical" means. They are reported with every diff.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from whydiff import defaults


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
        description="Round decimals to this many digits after the point; None compares exactly.",
    )
    float_digits: int | None = Field(
        default=defaults.FLOAT_DIGITS,
        ge=1,
        le=17,
        description="Round floats to this many significant digits; None compares exactly.",
    )
    trim_strings: bool = Field(default=False, description="Strip leading and trailing spaces.")
    case_insensitive: bool = Field(default=False, description="Lowercase strings.")
