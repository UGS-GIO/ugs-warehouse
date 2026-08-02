"""Topic primitives.

A `Topic` = a `{schema}.{layer}_current` Postgres serving table the warehouse
ingests. There is no hard-coded registry: topics are discovered at runtime via
`source.discover()` or carried in the Pub/Sub trigger payload, so adding a new
`_current` upstream is zero-config here.

`MART_SCHEMAS` is the small list of dbt mart schemas the warehouse considers —
discovery only scans these.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# The dbt serving schemas discovery scans. Must match the real Postgres schema names
# (per dataELT #418: it's `gengis`, not `gen_gis`). `gwportal` is omitted — it lives in a
# separate DB, so the mapping-db discovery sweep can't reach it (needs its own connection).
MART_SCHEMAS: tuple[str, ...] = (
    "hazards", "emp", "gengis", "wetlands", "mapping", "geochron", "boreholes",
)

# Which serving-table generation to ingest: `_current` (public, default) or `_review` (gated,
# pre-release). The REVIEW build sets WAREHOUSE_TABLE_SUFFIX=_review AND the review output prefixes
# (WAREHOUSE_STAC_PREFIX=review/stac, …) so it discovers `_review` tables and writes the whole catalog
# under review/. Validated to `_<letters/underscores>` so it can be inlined into the discover LIKE.
TABLE_SUFFIX = os.environ.get("WAREHOUSE_TABLE_SUFFIX", "_current")
if not re.fullmatch(r"_[a-z_]+", TABLE_SUFFIX):
    TABLE_SUFFIX = "_current"

# Both halves land in SQL by interpolation (`source._describe` unquoted, `stream_transformed`
# quoted then dollar-quoted) — neither survives a `"` or a `$pgq$` in the identifier. `layer` comes
# from the Pub/Sub payload unchecked (the service gates only `schema`), so the guard goes on the
# type, where every construction path hits it. Same reason TABLE_SUFFIX is validated above.
# 63 = PostgreSQL's identifier length limit.
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}")


@dataclass(frozen=True)
class Topic:
    layer: str   # `_current` table name (= MapLibre source-layer)
    schema: str  # Postgres / dbt mart schema (= warehouse partition root)

    def __post_init__(self) -> None:
        """Reject anything that is not a bare SQL identifier (see IDENT_RE), on every construction
        path — `parse`, `from_pubsub`, `discover`."""
        for field, value in (("schema", self.schema), ("layer", self.layer)):
            if not isinstance(value, str) or not IDENT_RE.fullmatch(value):
                raise ValueError(
                    f"topic {field} must be a bare SQL identifier "
                    f"(letters/digits/underscore, <=63 chars); got {value!r}"
                )

    @property
    def stem(self) -> str:
        """Bare topic name, the serving suffix (`_current`/`_review`) stripped — so a review layer
        gets the SAME artifact stem as its public counterpart (only the output prefix differs)."""
        return self.layer.removesuffix(TABLE_SUFFIX)

    @property
    def fqn(self) -> str:
        return f"{self.schema}.{self.layer}"

    @classmethod
    def parse(cls, dotted: str) -> "Topic":
        """Parse a `schema.layer_current` string into a Topic."""
        if "." not in dotted:
            raise ValueError(
                f"topic must be 'schema.layer_current' (got {dotted!r})"
            )
        schema, layer = dotted.split(".", 1)
        return cls(layer=layer, schema=schema)


def from_pubsub(payload: dict) -> Topic:
    """Build a Topic from a publish-event payload `{schema, topic}`."""
    schema = payload.get("schema")
    layer = payload.get("topic") or payload.get("layer")
    if not schema or not layer:
        raise ValueError(f"payload missing schema/topic: {payload}")
    return Topic(layer=layer, schema=schema)


