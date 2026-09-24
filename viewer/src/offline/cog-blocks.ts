// Reassembling a byte range from stored COG blocks, and reading their meta.json. Its own module,
// free of geotiff, because the service worker uses it too and must not pull that into its bundle.
import { type Bbox, bboxesOf, isRecord, optionalNumber, optionalString } from "./guards";

/** meta.json of a file stored by blocks (a COG or table saved by area). */
export type BlockMeta = { size: number; block: number; bboxes: Bbox[]; version?: string; savedAt?: number };

/** A block store's meta.json, checked field by field; null when missing or not one. */
export function parseBlockMeta(v: unknown): BlockMeta | null {
  if (!isRecord(v)) return null;
  const size = optionalNumber(v.size);
  const block = optionalNumber(v.block);
  if (size === undefined || block === undefined || block <= 0) return null;
  return { size, block, bboxes: bboxesOf(v.bboxes), version: optionalString(v.version), savedAt: optionalNumber(v.savedAt) };
}

/** The indices of the fixed-size blocks covering [start, start + length). */
export function blocksOf(start: number, length: number, block: number): number[] {
  const out: number[] = [];
  for (let b = Math.floor(start / block); b <= Math.floor((start + length - 1) / block); b++) out.push(b);
  return out;
}

/** A requested byte range [start, end] (inclusive) out of stored blocks, or null if one is missing. */
export function assemble(start: number, end: number, block: number,
  get: (index: number) => Uint8Array | null): Uint8Array<ArrayBuffer> | null {
  const out = new Uint8Array(end - start + 1);
  for (let b = Math.floor(start / block); b <= Math.floor(end / block); b++) {
    const data = get(b);
    if (!data) return null;
    const from = Math.max(start, b * block);
    const to = Math.min(end, b * block + data.length - 1);
    out.set(data.subarray(from - b * block, to - b * block + 1), from - start);
  }
  return out;
}
