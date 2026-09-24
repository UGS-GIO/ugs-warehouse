// Origin-private filesystem store for whole downloaded artifacts.
//
// PMTiles and COGs are single files read by HTTP Range, so "take this layer offline" means keeping
// the file, not warming a tile cache: the Cache API stores whole responses and cannot serve a 206
// out of one, which is why the service worker's runtime route skips range requests entirely.
// OPFS gives us a real File, which pmtiles reads through its FileSource with no other changes.
//
// The URL is the filename (percent-encoded, so no "/" survives), so a directory listing IS the
// manifest and there is no second store to keep in sync.

import { track } from "./in-flight";
import { fileNameFor, urlFromFileName, versionOf } from "./opfs-name";

export { fileNameFor, urlFromFileName };

const DIR = "layers";

/** Stored artifact: where it came from, what it costs, and when it was saved (epoch ms). */
export type StoredFile = { url: string; bytes: number; savedAt: number };

export type SaveOptions = {
  signal?: AbortSignal;
  /** Called with bytes written so far and the total when the server declared one. */
  onProgress?: (written: number, total?: number) => void;
};

/** True when this browser can store artifacts at all (OPFS + a writable stream). */
export const isSupported = (): boolean =>
  typeof navigator !== "undefined" && !!navigator.storage?.getDirectory;

async function dir(create = false): Promise<FileSystemDirectoryHandle | null> {
  if (!isSupported()) return null;
  try {
    const root = await navigator.storage.getDirectory();
    return await root.getDirectoryHandle(DIR, { create });
  } catch {
    return null;   // absent and not creating, or storage blocked (private window, policy)
  }
}

/** The stored file for `url`, or null when it was never downloaded. */
export async function get(url: string): Promise<File | null> {
  const d = await dir();
  if (!d) return null;
  try {
    return await (await d.getFileHandle(fileNameFor(url))).getFile();
  } catch {
    return null;
  }
}

/**
 * Where a (re)started download begins, given what is already on disk and how the server answered.
 * A 206 whose range starts at our byte count continues the file; anything else (a 200 because the
 * file changed and If-Range failed, or a server that ignores Range) starts it over.
 */
export function resumeFrom(have: number, status: number, contentRange: string | null,
  contentLength: string | null): { start: number; total?: number } {
  const m = contentRange && /^bytes (\d+)-\d+\/(\d+)$/.exec(contentRange);
  if (have > 0 && status === 206 && m && Number(m[1]) === have) return { start: have, total: Number(m[2]) };
  return { start: 0, total: Number(contentLength) || undefined };
}

/** Commit written bytes this often, so a closed tab or dropped connection loses at most this much. */
const COMMIT_EVERY = 8 * 1024 * 1024;

/**
 * Download `url` into OPFS, streaming so a 300 MB layer never sits in memory whole, and resuming
 * from where an earlier attempt stopped.
 *
 * Writes through `<name>.part` and renames on completion, so a partial file is never read as a
 * valid archive. The partial is KEPT when a download fails or the tab closes, with the file's
 * ETag beside it; the next attempt asks for the rest with `Range` + `If-Range`, which the server
 * honours only if the file is unchanged (otherwise it sends it whole and we start over).
 */
export function save(url: string, opts: SaveOptions = {}): Promise<StoredFile> {
  // Every download goes through here, so this is the one place that counts them for the
  // leave-page guard and the on-screen notice.
  return track(saveOnce(url, opts));
}

async function saveOnce(url: string, { signal, onProgress }: SaveOptions): Promise<StoredFile> {
  const d = await dir(true);
  if (!d) throw new Error("This browser cannot store layers offline (no OPFS).");

  const name = fileNameFor(url);
  const tmp = `${name}.part`;
  const side = `${tmp}.json`;
  const handle = await d.getFileHandle(tmp, { create: true });
  const have = (await handle.getFile()).size;
  const saved = have
    ? await d.getFileHandle(side).then((h) => h.getFile()).then((f) => f.text())
      .then((t) => JSON.parse(t) as { validator?: string; version?: string }).catch(() => undefined)
    : undefined;

  const headers: Record<string, string> = {};
  if (have && saved?.validator) { headers.range = `bytes=${have}-`; headers["if-range"] = saved.validator; }
  let res = await fetch(url, { signal, headers });
  if (!res.ok) throw new Error(`Download failed: ${res.status} ${res.statusText}`);
  // If-Range is only a request: our CDN ignores it and answers 206 from a file that has since
  // changed. The rest of a different file would corrupt the partial, so check the version ourselves
  // and start over when it moved.
  if (res.status === 206 && (!saved?.version || versionOf(res.headers) !== saved.version)) {
    await res.body?.cancel();
    res = await fetch(url, { signal });
    if (!res.ok) throw new Error(`Download failed: ${res.status} ${res.statusText}`);
  }
  if (!res.body) throw new Error("Download failed: response had no body to stream.");

  const { start, total } = resumeFrom(have, res.status, res.headers.get("content-range"), res.headers.get("content-length"));
  if (start === 0) {
    // A fresh start: remember what identifies this version, for If-Range and the check above next
    // time. A weak ETag can't be used for a range condition, so Last-Modified stands in there.
    const etag = res.headers.get("etag");
    const validator = etag && !etag.startsWith("W/") ? etag : res.headers.get("last-modified");
    const w = await (await d.getFileHandle(side, { create: true })).createWritable();
    await w.write(JSON.stringify({ validator: validator ?? undefined, version: versionOf(res.headers) }));
    await w.close();
  }

  let out = await handle.createWritable({ keepExistingData: start > 0 });
  if (start > 0) await out.seek(start);
  let written = start;
  let sinceCommit = 0;
  try {
    const reader = res.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      await out.write(value);
      written += value.byteLength;
      sinceCommit += value.byteLength;
      onProgress?.(written, total);
      if (sinceCommit >= COMMIT_EVERY) {
        // Nothing reaches disk until the writer closes; commit and carry on where we were.
        await out.close();
        out = await handle.createWritable({ keepExistingData: true });
        await out.seek(written);
        sinceCommit = 0;
      }
    }
    await out.close();
  } catch (e) {
    // Commit what arrived so the next attempt resumes from it, rather than discarding it.
    await out.close().catch(() => out.abort().catch(() => {}));
    throw e;
  }

  // `move` is how OPFS renames. Where it is missing, copy through a second write rather than
  // leaving the artifact parked under the .part name.
  const movable = handle as FileSystemFileHandle & { move?: (name: string) => Promise<void> };
  if (movable.move) {
    await movable.move(name);
  } else {
    const final = await (await d.getFileHandle(name, { create: true })).createWritable();
    // Write the File itself, not its arrayBuffer: `write` takes a Blob, and buffering a 300 MB
    // archive to rename it would undo the streaming above and can take the tab down.
    await final.write(await handle.getFile());
    await final.close();
    await d.removeEntry(tmp).catch(() => {});
  }
  await d.removeEntry(side).catch(() => {});
  return { url, bytes: written, savedAt: Date.now() };
}

/** Throw away a partial download (the user removed it from the queue). */
export async function discardPartial(url: string): Promise<void> {
  const d = await dir();
  await d?.removeEntry(`${fileNameFor(url)}.part`).catch(() => {});
  await d?.removeEntry(`${fileNameFor(url)}.part.json`).catch(() => {});
}

/** Downloads stopped part-way: what each has written so far, kept so it can resume. */
export async function listPartials(): Promise<StoredFile[]> {
  const d = await dir();
  const out: StoredFile[] = [];
  if (!d) return out;
  for await (const [name, h] of d) {
    if (h.kind !== "file" || !name.endsWith(".part")) continue;
    const f = await (h as FileSystemFileHandle).getFile().catch(() => null);
    if (f) out.push({ url: urlFromFileName(name.slice(0, -".part".length)), bytes: f.size, savedAt: f.lastModified });
  }
  return out;
}

/**
 * Delete partial downloads no queued job will resume (its job was removed, or the queue was lost),
 * and sidecars left without their partial. `keep` is the URLs the queue still means to download.
 */
export async function sweepPartials(keep: ReadonlySet<string>): Promise<number> {
  const d = await dir();
  if (!d) return 0;
  const names: string[] = [];
  for await (const [name] of d) names.push(name);
  const have = new Set(names);
  let freed = 0;
  for (const name of names) {
    const base = name.endsWith(".part") ? name.slice(0, -".part".length)
      : name.endsWith(".part.json") ? name.slice(0, -".part.json".length) : null;
    if (base === null) continue;
    const orphanSidecar = name.endsWith(".json") && !have.has(`${base}.part`);
    if (!orphanSidecar && keep.has(urlFromFileName(base))) continue;
    if (name.endsWith(".part")) freed += (await (await d.getFileHandle(name)).getFile()).size;
    await d.removeEntry(name).catch(() => {});
  }
  return freed;
}

/** Forget one stored artifact. Silent when it was not stored. */
export async function remove(url: string): Promise<void> {
  const d = await dir();
  await d?.removeEntry(fileNameFor(url)).catch(() => {});
}

/** Every stored artifact, newest-first order not guaranteed. Skips in-flight `.part` files. */
export async function list(): Promise<StoredFile[]> {
  const d = await dir();
  if (!d) return [];
  const out: StoredFile[] = [];
  // Iterating the handle yields [name, handle] PAIRS, not bare handles. Treating an entry as a
  // handle type-checks and then fails at runtime with "h.getFile is not a function".
  for await (const [name, h] of d) {
    if (h.kind !== "file" || name.endsWith(".part") || name.endsWith(".part.json")) continue;
    try {
      const file = await (h as FileSystemFileHandle).getFile();
      out.push({ url: urlFromFileName(name), bytes: file.size, savedAt: file.lastModified });
    } catch {
      // One locked or unreadable entry must not take down the listing, which is what every
      // offline control in the UI renders from.
    }
  }
  return out;
}

/**
 * Whether `bytes` plausibly fits in what the origin is still allowed to store.
 *
 * Deliberately conservative: browsers report quota in large, approximate units and start evicting
 * under pressure, so this keeps a 10% margin rather than filling to the reported line. Unknown
 * quota means we let the download proceed; a failed write reports itself.
 */
export function fitsInQuota(bytes: number, estimate?: { usage?: number; quota?: number }): boolean {
  const { usage = 0, quota } = estimate ?? {};
  if (!quota) return true;
  return bytes <= (quota - usage) * 0.9;
}

// Chrome 133+ reports usage + 10 GiB for every site, whatever the real limit (about 60% of the
// disk), so estimate() can't reveal incognito mode. That number is not a ceiling; treat it as unknown.
const PLACEHOLDER_HEADROOM = 10 * 1024 ** 3;

/** Bytes stored by this origin and the ceiling the browser will allow, when it says. */
export async function quota(): Promise<{ usage?: number; quota?: number }> {
  if (typeof navigator === "undefined" || !navigator.storage?.estimate) return {};
  const e = await navigator.storage.estimate();
  return realQuota(e);
}

/** Drop Chrome's placeholder quota, keep a real one. */
export function realQuota(e: { usage?: number; quota?: number }): { usage?: number; quota?: number } {
  return e.quota !== undefined && e.quota - (e.usage ?? 0) === PLACEHOLDER_HEADROOM ? { usage: e.usage } : e;
}

/** "12.4 MB" — decimal units, matching what a browser's storage panel reports. */
export function formatBytes(bytes: number): string {
  if (bytes < 1000) return `${bytes} B`;
  const units = ["kB", "MB", "GB", "TB"];
  let n = bytes / 1000;
  let i = 0;
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
  // One decimal below 100, so a layer reads as "12.4 MB" rather than "12 MB"; past that the
  // fraction is noise in a list row.
  return `${n < 100 ? n.toFixed(1) : Math.round(n)} ${units[i]}`;
}
