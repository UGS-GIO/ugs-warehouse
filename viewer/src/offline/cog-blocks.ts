// Reassembling a byte range from stored COG blocks. Its own module, with no imports, because the
// service worker uses it too and must not pull geotiff into its bundle.

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
