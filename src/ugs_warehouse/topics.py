"""Topic primitives.

A `Topic` = a `{schema}.{layer}_current` Postgres serving table the warehouse
ingests. There is no hard-coded registry: topics are discovered from Postgres
at runtime (or carried in the Pub/Sub trigger payload), so adding a new
`_current` upstream is zero-config here.

`MART_SCHEMAS` is the small list of dbt mart schemas the warehouse considers —
discovery only scans these.
"""
from __future__ import annotations

from dataclasses import dataclass

import duckdb

MART_SCHEMAS: tuple[str, ...] = ("hazards", "emp", "gen_gis")


@dataclass(frozen=True)
class Topic:
    layer: str   # `_current` table name (= MapLibre source-layer)
    schema: str  # Postgres / dbt mart schema (= warehouse partition root)

    @property
    def stem(self) -> str:
        """Bare topic name, `_current` suffix stripped."""
        return self.layer.removesuffix("_current")

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


def discover(
    con: duckdb.DuckDBPyConnection,
    pg_alias: str = "pg",
    schemas: tuple[str, ...] = MART_SCHEMAS,
) -> list[Topic]:
    """Every `_current` table in the configured mart schemas.

    Queries Postgres `information_schema.tables` through DuckDB's postgres
    extension (the connection must already have Postgres ATTACHed as
    `pg_alias` — see `source._connect`).
    """
    schema_list = ",".join(f"'{s}'" for s in schemas)
    pg_sql = (
        "SELECT table_schema, table_name FROM information_schema.tables "
        r"WHERE table_name LIKE '%\_current' ESCAPE '\' "
        f"AND table_schema IN ({schema_list}) "
        "ORDER BY table_schema, table_name"
    )
    rows = con.execute(
        "SELECT * FROM postgres_query(?, ?)", [pg_alias, pg_sql]
    ).fetchall()
    return [Topic(schema=s, layer=t) for s, t in rows]
