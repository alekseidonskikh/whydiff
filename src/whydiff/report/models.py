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
    total: int
    differing: int
    inspected: int


class ColumnChange(BaseModel):
    column: str
    rows_changed: int


class RowSample(BaseModel):
    kind: str  # "removed" | "added" | "changed"
    key: dict[str, str | None]
    source: dict[str, str | None] | None = None
    target: dict[str, str | None] | None = None
    changed_columns: list[str] = Field(default_factory=list)


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
    truncated: bool = Field(
        default=False,
        description="True when not every differing bucket was inspected row by row.",
    )
    elapsed_seconds: float = 0.0
