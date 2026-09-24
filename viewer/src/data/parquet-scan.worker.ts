// Runs the table's free-text search (parquet-lite.ts scanUntil) off the main thread, so a long
// scan never freezes the page. One search at a time: a new term replaces the last, which stops
// at its next row group.
import { asyncBufferFromUrl } from "hyparquet";
import { newScan, type Opened, openWith, rowOf, type Scan, scanPage, scanUntil, searchUrl } from "./parquet-lite";

// A page request, or (with featureId) where a map-clicked feature sits among the matches.
export type ScanRequest = { id: number; url: string; term: string; offset: number; limit: number; featureId?: number };

type Current = {
  url: string; term: string; scan: Scan; queue: Promise<unknown>;
  opened: Promise<Opened>; sidecar: Promise<Opened | null>;   // null: a file from before the sidecar
};
let current: Current | null = null;

// The scan is let go after a minute with no search, so a table left behind holds no memory.
const IDLE_MS = 60_000;
let pending = 0;
let idle: ReturnType<typeof setTimeout> | undefined;

self.onmessage = (e: MessageEvent<ScanRequest>) => {
  const { id, url, term, offset, limit, featureId } = e.data;
  pending++;
  clearTimeout(idle);
  if (!current || current.url !== url || current.term !== term) {
    // Not cached: a scan can read hundreds of MB, which a byte cache would keep in memory.
    current = {
      url, term, scan: newScan(term), queue: Promise.resolve(),
      opened: asyncBufferFromUrl({ url }).then(openWith),
      sidecar: asyncBufferFromUrl({ url: searchUrl(url) }).then(openWith).catch(() => null),
    };
  }
  const mine = current;
  const alive = () => current === mine;
  // One request at a time per scan: two at once would read the same row group and add its matches twice.
  mine.queue = mine.queue.then(async () => {
    try {
      const [o, sidecar] = await Promise.all([mine.opened, mine.sidecar]);
      if (featureId !== undefined) {
        // Hits are in file order, so the scan can stop once it passes the clicked row.
        const row = await rowOf(o, featureId);
        if (row !== null) await scanUntil(o, sidecar, mine.scan, Infinity, () => alive() && (mine.scan.hits.at(-1) ?? -1) < row);
        const at = row === null ? -1 : mine.scan.hits.indexOf(row);
        self.postMessage({ id, ordinal: at < 0 ? null : at });
      } else {
        // One row past the page, so a next page is offered only when it has rows.
        await scanUntil(o, sidecar, mine.scan, offset + limit + 1, alive);
        self.postMessage({ id, page: await scanPage(o, mine.scan, { limit, offset }) });
      }
    } catch (err) {
      // Start over on the next request, or a failed open would fail every retry of this term.
      if (current === mine) current = null;
      self.postMessage({ id, error: err instanceof Error ? err.message : String(err) });
    }
    if (--pending === 0) idle = setTimeout(() => { current = null; }, IDLE_MS);
  });
};
