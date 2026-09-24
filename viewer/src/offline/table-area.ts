// Save part of a data table (GeoParquet) for offline use: the row groups inside an area.
//
// The warehouse writes each table Hilbert-sorted with per-row bbox columns, so nearby rows share
// row groups and each group's footer stats give its extent. Saving the file's header, its footer
// and the groups that overlap the area is enough for DuckDB to answer any query clipped to that
// area (download.ts clipClause); the stats let it skip every other group without reading it.
//
// Stored like a COG saved by area (cog-area.ts): fixed blocks, served by the service worker.
import type { Bbox } from "./area";
import type { CogPlan } from "./block-store";
import { overlaps } from "@/lib/bbox";
import { blocksOf } from "./cog-blocks";
import { live, versionOf } from "./opfs-name";

/** Bigger than a COG block: row groups are contiguous runs of MB, not scattered 64 KB tiles. */
export const TABLE_BLOCK = 256 * 1024;

export type TableGroup = { xmin: number; ymin: number; xmax: number; ymax: number; start: number; end: number };


// DuckDB opens a parquet file by reading its last 64 KiB in one go, footer or not.
const TAIL_READ = 64 * 1024;

/**
 * The blocks to save for `bbox`: the header (magic bytes), the footer (metadata plus its length and
 * magic, the last `footerLength + 8` bytes, or DuckDB's larger tail read) and every row group
 * overlapping the area.
 */
export function planTableBlocks(url: string, size: number, footerLength: number, groups: TableGroup[],
  bbox: Bbox, block = TABLE_BLOCK): CogPlan & { bbox: Bbox } {
  const need = new Set<number>();
  const add = (start: number, end: number) => { for (const b of blocksOf(start, end - start, block)) need.add(b); };
  add(0, 4);
  add(Math.max(0, size - Math.max(footerLength + 8, TAIL_READ)), size);
  let tiles = 0;
  for (const g of groups) {
    if (!overlaps([g.xmin, g.ymin, g.xmax, g.ymax], bbox)) continue;
    tiles++;
    add(g.start, g.end);
  }
  const blocks = [...need].sort((a, b) => a - b);
  const bytes = blocks.reduce((n, b) => n + Math.min(block, size - b * block), 0);
  return { url, size, block, blocks, bytes, tiles, bbox };
}

/** Plan against the live file: its size, footer length and row groups, all read from the footer. */
export async function planTableArea(url: string, bbox: Bbox): Promise<CogPlan & { bbox: Bbox }> {
  const tail = await fetch(live(url), { headers: { range: "bytes=-8" }, cache: "no-store" });
  // A 200 would be the whole file, and its first bytes are not the footer length.
  if (tail.status !== 206) throw new Error(`The server did not answer a range request (${tail.status}).`);
  const size = Number(tail.headers.get("content-range")?.split("/")[1]);
  const version = versionOf(tail.headers);
  // As for a COG (cog-area.ts): DuckDB reads the footer through the service worker, so a saved
  // copy of an older version goes first. The tail read above already named the live version.
  await (await import("./block-store")).dropIfStale(url, version);
  const footerLength = new DataView(await tail.arrayBuffer()).getUint32(0, true);
  const { rowGroupSpans } = await import("@/data/download");
  const groups = await rowGroupSpans(url);
  if (groups.some((g) => !Number.isFinite(g.xmin))) throw new Error("This table has no bbox columns to cut by.");
  return { ...planTableBlocks(url, size, footerLength, groups, bbox), version };
}
