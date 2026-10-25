// PLSS townships and sections typed into the map search ("T3S R2E sec 12"), found in the
// warehouse's PLSS GeoParquet archives by their `label` ("T3S R2E") and section number. Online
// only: the lookup reads the archives by HTTP range.
import { BBOX_COLS, unionBbox } from "@/data/columns";
import type { Bounds } from "./place-locator";

export type Plss = { township: string; section?: number };
export type PlssHit = { label: string; meridian: string; bbox: Bounds };

// The serving-topic items the lookup reads.
export const PLSS_TOWNSHIPS = "enmin_plss_townshiprange";
export const PLSS_SECTIONS = "enmin_plss_sections";

// basemeridian codes (BLM CadNSDI) for the two meridians Utah is surveyed from.
const MERIDIANS: Record<string, string> = { "26": "Salt Lake Meridian", "30": "Uintah Meridian" };

const TOWNSHIP = /\bT\.?\s*0*(\d{1,2})\s*([NS])\b\.?/i;
const RANGE = /\bR\.?\s*0*(\d{1,2})\s*([EW])\b\.?/i;
const SECTION = /^(?:(?:SEC(?:TION)?|S)\.?\s*0*(\d{1,2}))?$/i;

/** "T3S R2E", "t3s r2e sec 12", "T3S R2E S12", "Sec. 12, T3S, R2E", or null. */
export function parsePlss(text: string): Plss | null {
  const t = TOWNSHIP.exec(text);
  const r = RANGE.exec(text);
  if (!t || !r) return null;
  const s = SECTION.exec(text.replace(t[0], "").replace(r[0], "").replace(/[\s,]+/g, ""));
  if (!s) return null;
  const section = s[1] ? Number(s[1]) : undefined;
  if (section !== undefined && (section < 1 || section > 36)) return null;
  return { township: `T${t[1]}${t[2].toUpperCase()} R${r[1]}${r[2].toUpperCase()}`, section };
}

/** The township or section in each meridian that has one, outlined by its bbox. */
export async function findPlss(p: Plss, urls: { townships?: string; sections?: string }): Promise<PlssHit[]> {
  const { readWhere } = await import("@/data/parquet-lite");
  const url = p.section ? urls.sections : urls.townships;
  if (!url) return [];
  const keys = p.section ? ["label", "snum", "basemeridian"] : ["label", "basemeridian"];
  const rows = await readWhere(url, keys,
    (r) => r.label === p.township && (!p.section || Number(r.snum) === p.section), BBOX_COLS);
  const name = p.section ? `${p.township} Sec. ${p.section}` : p.township;
  const meridians = [...new Set(rows.map((r) => String(r.basemeridian)))];
  return meridians.flatMap((m) => {
    const bbox = unionBbox(rows.filter((r) => String(r.basemeridian) === m));
    return bbox ? [{ label: name, meridian: MERIDIANS[m] ?? `Meridian ${m}`, bbox }] : [];
  });
}
