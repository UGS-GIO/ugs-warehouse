// Runs the table's free-text search (parquet-lite.ts scanUntil) off the main thread, so a long
// scan never freezes the page. One search at a time: a new term replaces the last, which stops
// at its next row group.
import { asyncBufferFromUrl } from "hyparquet";
import { newScan, type Opened, openWith, type Scan, scanOrdinal, scanPage, scanUntil } from "./parquet-lite";

// A page request, or (with featureId) where a map-clicked feature sits among the matches.
export type ScanRequest = { id: number; url: string; term: string; offset: number; limit: number; featureId?: number };

type Current = { url: string; term: string; opened: Promise<Opened>; scan: Scan; queue: Promise<unknown> };
let current: Current | null = null;

// The matched rows are let go after a minute with no search, so a table left behind holds no memory.
const IDLE_MS = 60_000;
let pending = 0;
let idle: ReturnType<typeof setTimeout> | undefined;

self.onmessage = (e: MessageEvent<ScanRequest>) => {
  const { id, url, term, offset, limit, featureId } = e.data;
  pending++;
  clearTimeout(idle);
  if (!current || current.url !== url || current.term !== term) {
    // Not cached: a scan can read hundreds of MB, which a byte cache would keep in memory.
    current = { url, term, opened: asyncBufferFromUrl({ url }).then(openWith), scan: newScan(term), queue: Promise.resolve() };
  }
  const mine = current;
  const alive = () => current === mine;
  // One request at a time per scan: two at once would read the same row group and add its matches twice.
  mine.queue = mine.queue.then(async () => {
    try {
      const o = await mine.opened;
      if (featureId !== undefined) {
        await scanUntil(o, mine.scan, Infinity, () => alive() && scanOrdinal(mine.scan, featureId) < 0);
        const at = scanOrdinal(mine.scan, featureId);
        self.postMessage({ id, ordinal: at < 0 ? null : at });
      } else {
        // One row past the page, so a next page is offered only when it has rows.
        await scanUntil(o, mine.scan, offset + limit + 1, alive);
        self.postMessage({ id, page: scanPage(o, mine.scan, { limit, offset }) });
      }
    } catch (err) {
      // Start over on the next request, or a failed open would fail every retry of this term.
      if (current === mine) current = null;
      self.postMessage({ id, error: err instanceof Error ? err.message : String(err) });
    }
    if (--pending === 0) idle = setTimeout(() => { current = null; }, IDLE_MS);
  });
};
