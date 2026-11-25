"""The diff result. Serializes to JSON for CI and tooling."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from whydiff.normalize.rules import NormalizationRules


class Status(str, Enum):
    IDENTICAL = "identical"
    DIFFERENT = "different"


class TypeMismatch(BaseModel):
    column: str
    source_type: str
    target_type: str
    compared_as: str


class SchemaDiff(BaseModel):
    compared_columns: list[str]
    only_in_source: list[str] = Field(default_factory=list)
    only_in_target: list[str] = Field(default_factory=list)
    type_mismatches: list[TypeMismatch] = Field(default_factory=list)

    @property
    def differs(self) -> bool:
        return bool(self.only_in_source or self.only_in_target)


class RowCounts(BaseModel):
    source: int
    target: int


class BucketStats(BaseModel):
    total: int = Field(description="Buckets in the first pass.")
    differing: int = Field(description="Buckets whose checksums differ in the first pass.")
    bisection_levels: int = Field(default=0, description="Extra passes that split buckets.")
    segments: int = Field(default=0, description="Differing segments after bisection.")
    inspected: int = Field(default=0, description="Segments downloaded and compared.")
    rows_downloaded: int = Field(default=0, description="Rows fetched from both sides.")


class ColumnChange(BaseModel):
    column: str
    rows_changed: int


class RowSample(BaseModel):
    kind: str  # "removed" | "added" | "changed"
    key: dict[str, str | None]
    source: dict[str, str | None] | None = None
    target: dict[str, str | None] | None = None
    changed_columns: list[str] = Field(default_factory=list)


class Diagnosis(BaseModel):
    """A likely reason for some of the differences."""

    kind: str = Field(description="e.g. timezone, rounding, truncation, range, concentrated")
    scope: str = Field(description="changed, removed or added rows")
    column: str | None = None
    rows: int = Field(description="Rows the finding explains.")
    of_rows: int = Field(default=0, description="Rows it was checked against.")
    message: str


class DiffResult(BaseModel):
    status: Status
    source: str
    target: str
    key: list[str]
    where: str | None = None
    rules: NormalizationRules
    schema_diff: SchemaDiff
    counts: RowCounts
    buckets: BucketStats
    removed: int = 0
    added: int = 0
    changed: int = 0
    duplicate_keys_source: int = 0
    duplicate_keys_target: int = 0
    column_changes: list[ColumnChange] = Field(default_factory=list)
    samples: list[RowSample] = Field(default_factory=list)
    diagnoses: list[Diagnosis] = Field(default_factory=list)
    truncated: bool = Field(
        default=False,
        description="True when not every differing segment was inspected row by row.",
    )
    elapsed_seconds: float = 0.0
