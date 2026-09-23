// What a stored file IS, for the offline manager. OPFS only knows URLs and bytes; a person needs
// "Quaternary Faults" or "Basemap quad 40111g8", and whether a newer version has been published.
import { BASEMAP_BASE, overviewUrl } from "./basemap";
import type { StoredFile } from "./opfs";

export type Described = StoredFile & {
  kind: "layer" | "basemap";
  label: string;
  /** The catalog item a layer came from, so the manager can link back to it. */
  itemHref?: string;
  /** True when the catalog says the item changed after this copy was saved. */
  stale: boolean;
};

/** The slice of a catalog item this needs: its title, when it last changed, and where its files are. */
export type ItemLike = {
  href: string;
  data?: {
    properties?: { title?: string; updated?: string };
    assets?: Record<string, { href: string }>;
    links?: { href: string }[];
  };
};

// A layer's PMTiles archive is published as a rel="pmtiles" LINK, while COGs and parquet are
// assets, so both have to be searched to find the item a stored file came from.
const hrefsOf = (it: ItemLike): string[] => [
  ...Object.values(it.data?.assets ?? {}).map((a) => a.href),
  ...(it.data?.links ?? []).map((l) => l.href),
];

const QUAD = /\/quads\/([0-9]{5}[a-h][1-8])\.pmtiles$/;

export function describe(file: StoredFile, items: ItemLike[], base = BASEMAP_BASE): Described {
  if (file.url === overviewUrl(base)) {
    return { ...file, kind: "basemap", label: "Basemap overview (statewide)", stale: false };
  }
  const quad = file.url.startsWith(base) ? QUAD.exec(file.url)?.[1] : undefined;
  if (quad) return { ...file, kind: "basemap", label: `Basemap quad ${quad}`, stale: false };

  const item = items.find((it) => hrefsOf(it).includes(file.url));
  const updated = item?.data?.properties?.updated;
  const updatedAt = updated ? Date.parse(updated) : NaN;
  return {
    ...file,
    kind: "layer",
    label: item?.data?.properties?.title ?? decodeURIComponent(file.url.split("/").pop() ?? file.url),
    itemHref: item?.href,
    stale: Number.isFinite(updatedAt) && file.savedAt < updatedAt,
  };
}

/** Layers first, then the basemap; the overview leads the basemap group, then quads by code. */
export function sortDescribed(rows: Described[]): Described[] {
  const rank = (r: Described) => (r.kind === "layer" ? 0 : r.label.startsWith("Basemap overview") ? 1 : 2);
  return [...rows].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}
