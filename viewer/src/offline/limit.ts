// The What's here queue: pricing and point reads, four at a time, so a busy area does not open
// dozens of range requests at once on a phone connection.

let running = 0;
const waiting: (() => void)[] = [];

/** Runs `fn` when one of the four slots is free, and holds the slot until `fn` settles. A finishing
 *  task hands its slot straight to the next waiter, so a caller arriving in between cannot take it
 *  too and push the count past four. */
export async function limit<T>(fn: () => Promise<T>): Promise<T> {
  if (running >= 4) await new Promise<void>((r) => waiting.push(r));
  else running++;
  try { return await fn(); } finally {
    const next = waiting.shift();
    if (next) next(); else running--;
  }
}

/** The slots in use, for the tests. */
export const inFlight = () => running;

// A point read is a header and a tile; one slower than this is aborted and left out of the list.
export const PROBE_MS = 8_000;

/** `limit`, with a signal that fires when `signal` (the query's) does or after PROBE_MS. The slot
 *  is held until `fn` settles, so `fn` must stop on the signal for an abort to free it. */
export const probe = <T,>(fn: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> =>
  limit(async () => {
    // A timer, not AbortSignal.timeout: fake timers can drive it in the tests.
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(new Error("timed out")), PROBE_MS);
    try { return await fn(signal ? AbortSignal.any([ac.signal, signal]) : ac.signal); } finally { clearTimeout(timer); }
  });
