"""Fixed values that define how whydiff hashes data.

Changing anything in the "hash format" section changes every row hash, so
checksums computed by different whydiff versions stop matching. Treat it as
a format version.
"""

from __future__ import annotations

# -- hash format --------------------------------------------------------------

#: Token for a NULL value. A non-NULL value is encoded as ``<byte length>:<value>``
#: and so always starts with a digit; this token starts with ``<`` and therefore
#: never collides with a value, whatever the value is. It is plain ASCII with no
#: quotes or backslashes, so every database spells it the same way in SQL.
NULL_TOKEN = "<<NULL>>"

#: Separates a value's byte length from the value in its token.
LENGTH_SEPARATOR = ":"

#: Hex digits of md5 kept for a hash: 15 hex digits = 60 bits, which fits a
#: signed BIGINT in every database, while sums of millions of them still fit
#: NUMERIC / HUGEINT.
HASH_HEX_DIGITS = 15
HASH_BITS = HASH_HEX_DIGITS * 4
HASH_SPACE = 1 << HASH_BITS

#: Segment checksums are sums of row hashes taken modulo this. Postgres sums
#: exactly, DuckDB in HUGEINT and ClickHouse wraps at 64 bits, so the modulus
#: has to be part of the format for all of them to agree.
CHECKSUM_MODULUS = 1 << 64

#: Binary precision of single-precision floats. A single and a double column
#: are compared as singles: comparing as doubles would expose the rounding of
#: the single side in nearly every row.
FLOAT32_PRECISION = 24
FLOAT64_PRECISION = 53

# -- engine limits ------------------------------------------------------------

#: Upper bound for the number of buckets in the first pass.
MAX_BUCKETS = 1 << 18

#: Segment ids per ``IN (...)`` list, to keep each query a reasonable size.
IDS_PER_QUERY = 10_000

# -- logging ------------------------------------------------------------------

#: Name of the package logger; every module logs under it.
LOGGER_NAME = "whydiff"

#: SQL longer than this is shortened in DEBUG logs.
SQL_LOG_LIMIT = 2_000
