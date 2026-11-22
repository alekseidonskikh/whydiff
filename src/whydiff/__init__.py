"""whydiff: diff tables across databases and explain why they differ."""

__version__ = "0.1.0.dev0"

from whydiff.connectors import connect
from whydiff.engine import diff_tables
from whydiff.normalize import NormalizationRules
from whydiff.report import DiffResult, Status, render_text

__all__ = [
    "DiffResult",
    "NormalizationRules",
    "Status",
    "__version__",
    "connect",
    "diff_tables",
    "render_text",
]
