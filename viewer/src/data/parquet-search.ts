// Main-thread side of the table's free-text search: one worker, created on the first search, and
// a promise per request (parquet-scan.worker.ts does the scanning).
import type { Page } from "./download";
import type { ScanRequest } from "./parquet-scan.worker";

let worker: Worker | null = null;
let seq = 0;
const waiting = new Map<number, { resolve: (p: Page) => void; reject: (e: Error) => void }>();

function scanner(): Worker {
  if (!worker) {
    worker = new Worker(new URL("./parquet-scan.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<{ id: number; page?: Page; error?: string }>) => {
      const w = waiting.get(e.data.id);
      if (!w) return;
      waiting.delete(e.data.id);
      if (e.data.page) w.resolve(e.data.page); else w.reject(new Error(e.data.error ?? "search failed"));
    };
  }
  return worker;
}

/** A page of the rows matching `term` in any shown column. `complete` is false while matches remain unscanned. */
export function searchPage(url: string, term: string, range: { limit: number; offset: number }): Promise<Page> {
  const id = ++seq;
  const request: ScanRequest = { id, url, term, ...range };
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    scanner().postMessage(request);
  });
}
