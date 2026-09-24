// Main-thread side of the table's free-text search: one worker, created on the first search, and
// a promise per request (parquet-scan.worker.ts does the scanning).
import type { Page } from "./download";
import type { ScanRequest } from "./parquet-scan.worker";

type Reply = { id: number; page?: Page; ordinal?: number | null; error?: string };

let worker: Worker | null = null;
let seq = 0;
const waiting = new Map<number, { resolve: (r: Reply) => void; reject: (e: Error) => void }>();

function scanner(): Worker {
  if (!worker) {
    worker = new Worker(new URL("./parquet-scan.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<Reply>) => {
      const w = waiting.get(e.data.id);
      if (!w) return;
      waiting.delete(e.data.id);
      if (e.data.error === undefined) w.resolve(e.data); else w.reject(new Error(e.data.error));
    };
    // A worker that fails to load or dies fails what it holds; the next search starts a new one.
    worker.onerror = () => {
      for (const w of waiting.values()) w.reject(new Error("search failed"));
      waiting.clear();
      worker?.terminate();
      worker = null;
    };
  }
  return worker;
}

function ask(request: Omit<ScanRequest, "id">): Promise<Reply> {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    waiting.set(id, { resolve, reject });
    scanner().postMessage({ id, ...request });
  });
}

/** A page of the rows matching `term` in any shown column. `complete` is false while matches remain unscanned. */
export async function searchPage(url: string, term: string, range: { limit: number; offset: number }): Promise<Page> {
  const { page } = await ask({ url, term, ...range });
  if (!page) throw new Error("search failed");
  return page;
}

/** Where a feature sits among the matches for `term`, or null when it does not match. */
export async function searchOrdinal(url: string, term: string, featureId: number): Promise<number | null> {
  return (await ask({ url, term, offset: 0, limit: 0, featureId })).ordinal ?? null;
}
