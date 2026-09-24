// Runs the table's free-text search (parquet-lite.ts scanUntil) off the main thread, so a long
// scan never freezes the page. One search at a time: a new term replaces the last, which stops
// at its next row group.
import { asyncBufferFromUrl } from "hyparquet";
import { newScan, type Opened, openWith, type Scan, scanPage, scanUntil } from "./parquet-lite";

export type ScanRequest = { id: number; url: string; term: string; offset: number; limit: number };

let current: { url: string; term: string; opened: Promise<Opened>; scan: Scan } | null = null;

self.onmessage = async (e: MessageEvent<ScanRequest>) => {
  const { id, url, term, offset, limit } = e.data;
  try {
    if (!current || current.url !== url || current.term !== term) {
      // Not cached: a scan can read hundreds of MB, which a byte cache would keep in memory.
      current = { url, term, opened: asyncBufferFromUrl({ url }).then(openWith), scan: newScan(term) };
    }
    const mine = current;
    const o = await mine.opened;
    await scanUntil(o, mine.scan, offset + limit, () => current === mine);
    self.postMessage({ id, page: scanPage(o, mine.scan, { limit, offset }) });
  } catch (err) {
    self.postMessage({ id, error: err instanceof Error ? err.message : String(err) });
  }
};
