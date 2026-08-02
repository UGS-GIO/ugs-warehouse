"""SQL identifier validation for names that get interpolated into queries.

DuckDB binds values, not identifiers, and has no `quote_ident`. Escaping wouldn't be enough
anyway: `vector/source.stream_transformed` wraps its statement in a `$pgq$` fence that a name
containing that sequence terminates, and several of these names also become paths, GCS keys and
argv. So reject rather than escape.

`featureserv/gen_db.py` keeps its own copy — its image installs that file, not the package.
"""
from __future__ import annotations

import re

# 63 = PostgreSQL's identifier limit. Anchored: `.match` would accept a prefix plus an injection.
IDENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,62}\Z")


def is_identifier(value: object) -> bool:
    return isinstance(value, str) and IDENT_RE.fullmatch(value) is not None


def require_identifier(kind: str, value: object) -> str:
    """Return `value`, or raise ValueError. `kind` names the field so the error says what to fix."""
    if not is_identifier(value):
        raise ValueError(
            f"{kind} must be a bare SQL identifier "
            f"(letters/digits/underscore, <=63 chars); got {value!r}"
        )
    return value  # type: ignore[return-value]
