// HTTP Range arithmetic for serving a stored file out of the service worker.
//
// Split out and unit-tested because the clients are strict about 206 semantics and fail in ways
// that do not look like range bugs: pmtiles' FetchSource throws outright when a 200 arrives with a
// content-length larger than it asked for, and duckdb-wasm reads a parquet footer by a suffix range
// before it reads anything else. Getting this wrong makes a downloaded layer look corrupt.
//
// Not workbox-range-requests: it answers 416 to a range that runs past the end of the file, where
// HTTP says to send what exists. pmtiles opens every archive by asking for its first 16 KiB, so a
// smaller archive would not open, and a suffix longer than the file would fail the same way.

/** A resolved range over a file of known size. `end` is INCLUSIVE, as the HTTP header is. */
export type Resolved =
  | { kind: "full"; size: number }
  | { kind: "partial"; start: number; end: number; size: number }
  | { kind: "unsatisfiable"; size: number };

/**
 * Resolve a Range header against a file size.
 *
 * Handles the three forms clients actually send: `bytes=a-b`, `bytes=a-` (open-ended) and
 * `bytes=-n` (the last n bytes, which is how a parquet footer is read). Anything else — multiple
 * ranges, a non-bytes unit, a malformed value — resolves to the full body, which the spec allows
 * and every client accepts.
 */
export function resolveRange(header: string | null, size: number): Resolved {
  if (!header) return { kind: "full", size };

  const spec = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!spec) return { kind: "full", size };

  const [, rawStart, rawEnd] = spec;
  if (rawStart === "" && rawEnd === "") return { kind: "full", size };

  let start: number;
  let end: number;
  if (rawStart === "") {
    // Suffix range: the last N bytes. N larger than the file is the whole file, not an error.
    const n = Number(rawEnd);
    if (n === 0) return { kind: "unsatisfiable", size };
    start = Math.max(0, size - n);
    end = size - 1;
  } else {
    start = Number(rawStart);
    end = rawEnd === "" ? size - 1 : Math.min(Number(rawEnd), size - 1);
  }

  // A start at or past the end has no bytes to return; the spec's answer is 416, and clients rely
  // on it (pmtiles reads the total out of the 416's Content-Range to retry).
  if (start >= size || start > end) return { kind: "unsatisfiable", size };
  return { kind: "partial", start, end, size };
}

/** Response headers for a resolved range. `Accept-Ranges` on every reply keeps clients ranging. */
export function rangeHeaders(r: Resolved, contentType: string): Headers {
  const h = new Headers({ "Accept-Ranges": "bytes", "Content-Type": contentType });
  if (r.kind === "full") {
    h.set("Content-Length", String(r.size));
  } else if (r.kind === "partial") {
    h.set("Content-Range", `bytes ${r.start}-${r.end}/${r.size}`);
    h.set("Content-Length", String(r.end - r.start + 1));
  } else {
    h.set("Content-Range", `bytes */${r.size}`);
  }
  return h;
}

/** The status a resolved range replies with. */
export const rangeStatus = (r: Resolved): number =>
  r.kind === "partial" ? 206 : r.kind === "unsatisfiable" ? 416 : 200;

/** Content types for the artifacts we store, by extension. */
export function contentTypeFor(path: string): string {
  if (path.endsWith(".pmtiles")) return "application/octet-stream";
  if (path.endsWith(".tif") || path.endsWith(".tiff")) return "image/tiff";
  if (path.endsWith(".parquet")) return "application/vnd.apache.parquet";
  return "application/octet-stream";
}

/** Extensions the offline store serves. A request for anything else is left to the network. */
export const STORABLE = /\.(pmtiles|tiff?|parquet)$/i;
