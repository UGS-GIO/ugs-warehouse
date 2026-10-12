"""SQL identifier validation for names that get interpolated into queries.

DuckDB binds values, not identifiers, and has no `quote_ident`. Escaping wouldn't be enough
anyway: `vector/source.stream_transformed` wraps its statement in a `$pgq$` fence that a name
containing that sequence terminates, and several of these names also become paths, GCS keys and
argv. So reject rather than escape.
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


# Catalog-authored names (raster layer/item_id). Deliberately looser than IDENT_RE: ingest's
# sanitizer doesn't prefix a leading digit, so `30x60_quad_ofr123_20240601` is a real item_id.
# These reach object paths and quoted string literals, never an identifier position — excluding
# quotes, slashes and backslashes is the point, and a leading digit is harmless.
TOKEN_RE = re.compile(r"\A\w{1,63}\Z", re.ASCII)


def require_token(kind: str, value: object) -> str:
    """Return `value` if it's a bare [A-Za-z0-9_] token, else raise."""
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise ValueError(
            f"{kind} must be letters/digits/underscore (<=63 chars); got {value!r}"
        )
    return value


# Object-path segments allow `-` (STAC collection ids like `ugs-rasters`), which identifiers don't.
SEGMENT_RE = re.compile(r"\A[A-Za-z0-9_-]{1,63}\Z")


def require_object_path(kind: str, value: object) -> str:
    """Return `value` if it's a slash-joined path of safe segments, else raise.

    Rejects `..`, empty segments and absolute paths, so a value carrying one can't walk out of the
    prefix it was meant to be written under.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{kind} must be a non-empty path; got {value!r}")
    for segment in value.split("/"):
        if not SEGMENT_RE.fullmatch(segment):
            raise ValueError(
                f"{kind} has an unusable path segment {segment!r} "
                f"(letters/digits/underscore/hyphen, <=63 chars); got {value!r}"
            )
    return value
