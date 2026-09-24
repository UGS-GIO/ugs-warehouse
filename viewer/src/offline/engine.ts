// The table engine (duckdb-wasm) for offline use.
//
// Its loader and worker are precached with the app; the 36 MB .wasm is not, so everyone else does
// not pay for it. Saving a table for offline puts the .wasm in the "ugs-engine" cache, which the
// service worker answers from (sw.ts).
import { ENGINE_WASM } from "@/data/duckdb";

export const ENGINE_CACHE = "ugs-engine";
/** The .wasm's size, for pricing a save before it is fetched (duckdb-wasm 1.x `eh` build). */
export const ENGINE_BYTES = 36_000_000;

export async function saveEngine(onProgress?: (done: number, total?: number) => void): Promise<void> {
  const res = await fetch(ENGINE_WASM);
  if (!res.ok || !res.body) throw new Error(`Download failed: ${res.status}`);
  const total = Number(res.headers.get("content-length")) || undefined;
  let done = 0;
  // Counted on the way through, so the queue can show progress for a 36 MB file.
  const counted = res.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, c) { done += chunk.byteLength; onProgress?.(done, total); c.enqueue(chunk); },
  }));
  const cache = await caches.open(ENGINE_CACHE);
  await cache.put(ENGINE_WASM, new Response(counted, { headers: res.headers }));
}

export async function removeEngine(): Promise<void> {
  await caches.delete(ENGINE_CACHE);
}

export async function engineBytes(): Promise<number> {
  const r = await caches.match(ENGINE_WASM, { cacheName: ENGINE_CACHE }).catch(() => undefined);
  return Number(r?.headers.get("content-length")) || 0;
}
