"""whydiff: diff tables across databases and explain why they differ."""

import logging

from whydiff.connectors import connect
from whydiff.engine import diff_tables
from whydiff.normalize import NormalizationRules
from whydiff.report import DiffResult, Status, render_text

__version__ = "0.1.0.dev0"

# A library leaves logging to the application. Without this handler, Python's
# last-resort handler would print whydiff warnings to stderr in apps that
# never configured logging.
logging.getLogger("whydiff").addHandler(logging.NullHandler())

__all__ = [
    "DiffResult",
    "NormalizationRules",
    "Status",
    "__version__",
    "connect",
    "diff_tables",
    "render_text",
]
