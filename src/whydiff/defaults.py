"""Default settings. The Python API and the CLI both take their defaults from here.

Unlike :mod:`whydiff.constants`, these only tune speed and output; changing
them never changes what counts as a difference.
"""

from __future__ import annotations

# -- normalization --------------------------------------------------------------

#: Fractional second digits kept in timestamps (6 = microseconds).
TIMESTAMP_PRECISION = 6

#: Digits after the point for floats.
FLOAT_DIGITS = 6

# -- segmentation ---------------------------------------------------------------

#: Target rows per bucket in the first pass.
BUCKET_SIZE = 2_000

#: Differing segments with at most this many rows are downloaded, not split.
LEAF_SIZE = 64

#: Children per differing segment at each bisection level.
BISECTION_FACTOR = 32

#: Bisect only when the rows it would avoid downloading exceed this.
BISECTION_THRESHOLD = 10_000

#: Bisection levels at most; each level is one more scan per side.
MAX_BISECTION_LEVELS = 4

# -- output ---------------------------------------------------------------------

#: Rows downloaded per side at most; beyond this the result is ``truncated``.
MAX_ROWS = 1_000_000

#: Examples kept per kind of difference (removed, added, changed).
SAMPLE_LIMIT = 10
