// Geologic units by name or symbol ("Navajo Sandstone", "Ti"), from the distinct unit names and
// symbols in the geologic unit layers' GeoParquet archives: a few small dictionary-encoded columns,
// read on the first search. A pick zooms to where the unit is mapped, from the matching rows' bbox
// columns. Online only.
import type MiniSearch from "minisearch";
import type { ItemRef } from "@/catalog/browse";
import { BBOX_COLS, unionBbox } from "@/data/columns";
import { layerLabel, parquetAsset } from "@/stac";
import type { Bounds } from "./place-locator";

export type UnitHit = { symbol: string; name?: string; age?: string; layer: string; href: string;
  url: string; column: string };
export type UnitSearch = (q: string) => UnitHit[];

const LIMIT = 5;
const SYMBOL_COLS = ["unitsymbol", "mapunit"];
const NAME_COL = "unitname";
const AGE_COL = "age";

/** The geologic unit layers among the map's items, with their archive. */
export const unitLayers = (items: ItemRef[]) => items.flatMap((r) => {
  const url = r.data && /geolunits/.test(String(r.data.id)) ? parquetAsset(r.data)?.href : undefined;
  return url && r.data ? [{ href: r.href, url, title: layerLabel(r.data, String(r.data.id)) }] : [];
});

export async function buildUnitSearch(layers: ReturnType<typeof unitLayers>): Promise<UnitSearch> {
  const [{ default: MiniSearchCtor }, { readColumns }] =
    await Promise.all([import("minisearch"), import("@/data/parquet-lite")]);
  const units = new Map<string, UnitHit>();
  await Promise.all(layers.map(async (l) => {
    const rows = await readColumns(l.url, [...SYMBOL_COLS, NAME_COL, AGE_COL]);
    const column = SYMBOL_COLS.find((c) => rows[0] && c in rows[0]);
    if (!column) return;
    for (const r of rows) {
      const symbol = String(r[column] ?? "");
      if (!symbol || units.has(`${l.href}|${symbol}`)) continue;
      const text = (c: string) => (typeof r[c] === "string" && r[c] ? String(r[c]) : undefined);
      units.set(`${l.href}|${symbol}`, { symbol, name: text(NAME_COL), age: text(AGE_COL), layer: l.title,
        href: l.href, url: l.url, column });
    }
  }));
  const index: MiniSearch<{ id: string; symbol: string; name: string }> = new MiniSearchCtor({
    fields: ["symbol", "name"], searchOptions: { prefix: true, boost: { symbol: 2 } },
  });
  index.addAll([...units].map(([id, u]) => ({ id, symbol: u.symbol, name: u.name ?? "" })));
  return (q) => {
    const text = q.trim();
    if (text.length < 2) return [];
    // A typed symbol is case-sensitive in geology (Ti, TI): the exact one leads.
    const rank = (u: UnitHit) => (u.symbol === text ? 0 : u.symbol.toLowerCase() === text.toLowerCase() ? 1 : 2);
    return index.search(text).flatMap((h) => units.get(String(h.id)) ?? [])
      .map((u, i) => ({ u, i }))
      .sort((a, b) => rank(a.u) - rank(b.u) || a.i - b.i)
      .slice(0, LIMIT)
      .map(({ u }) => u);
  };
}

/** Where a unit is mapped: the extent of its polygons in its layer. */
export async function unitExtent(u: UnitHit): Promise<Bounds | undefined> {
  const { readWhere } = await import("@/data/parquet-lite");
  return unionBbox(await readWhere(u.url, [u.column], (r) => r[u.column] === u.symbol, BBOX_COLS));
}
