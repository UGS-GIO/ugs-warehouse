import type { ItemRef } from "@/catalog/browse";
import { validBbox } from "@/map/map-model";
import type { Bounds } from "@/map/place-locator";
import { idOf, type StacDoc } from "@/stac";

export type ItemHit = {
  kind: "layer" | "publication"; href: string; id: string; label: string; sub: string; bbox?: Bounds;
};
export type CatalogResults = { exact?: ItemHit; layers: ItemHit[]; publications: ItemHit[] };
export type CatalogSearch = (q: string) => CatalogResults;

const LIMIT = 5;
const NONE: CatalogResults = { layers: [], publications: [] };

function toHit(r: ItemRef & { data: StacDoc }, layer: boolean): ItemHit {
  const p = r.data.properties ?? {};
  const id = idOf(r.href);
  const kindLabel = layer ? p["ugs:topic"] ?? "Layer" : p["ugs:pub_type"] ?? p["ugs:series"];
  const year = String(p.datetime ?? "").slice(0, 4);
  return {
    kind: layer ? "layer" : "publication", href: r.href, id,
    label: String(p.title ?? id),
    sub: [layer ? undefined : id, kindLabel, layer ? undefined : year].filter(Boolean).join(" · "),
    bbox: validBbox(r.data.bbox),
  };
}

/**
 * The layer and publication half of the header search, over the items the catalog has loaded.
 * MiniSearch loads on first use, so it stays out of the main bundle. A series ID (`OFR-598`) is an
 * exact match, found by the same lookup Discover uses, and comes back on its own, ahead of the
 * ranked hits.
 */
export async function buildCatalogSearch(
  items: ItemRef[], isLayer: (r: ItemRef) => boolean, key: string,
): Promise<CatalogSearch> {
  const { catalogIndex, idMatch } = await import("@/discover/search-index");
  const hits = new Map<string, ItemHit>();
  // Keyed like the index's documents (toSearchDoc), so a hit's id finds its row.
  for (const r of items) {
    if (r.data) hits.set(`${r.collId}/${String(r.data.id)}`, toHit({ ...r, data: r.data }, isLayer(r)));
  }
  const { index } = catalogIndex(key, items);
  return (q) => {
    const text = q.trim();
    if (text.length < 2) return NONE;
    const named = idMatch(index, text);
    const exact = named ? hits.get(String(named.id)) : undefined;
    const found = index.search(text).flatMap((h) => {
      const hit = hits.get(String(h.id));
      return hit && hit !== exact ? [hit] : [];
    });
    return {
      exact,
      layers: found.filter((h) => h.kind === "layer").slice(0, LIMIT),
      publications: found.filter((h) => h.kind === "publication").slice(0, LIMIT),
    };
  };
}
