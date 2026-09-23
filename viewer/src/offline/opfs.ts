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
import { fileNameFor, urlFromFileName } from "./opfs-name";

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
 * Download `url` into OPFS, streaming so a 300 MB layer never sits in memory whole.
 *
 * Writes through a temp name and renames on completion, so an aborted or failed download cannot
 * leave a truncated file that later reads as a valid-looking archive.
 */
export function save(url: string, opts: SaveOptions = {}): Promise<StoredFile> {
  // Every download goes through here, so this is the one place that counts them for the
  // leave-page guard and the on-screen notice.
  return track(saveOnce(url, opts));
}

async function saveOnce(url: string, { signal, onProgress }: SaveOptions): Promise<StoredFile> {
  const d = await dir(true);
  if (!d) throw new Error("This browser cannot store layers offline (no OPFS).");

  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`Download failed: ${res.status} ${res.statusText}`);
  if (!res.body) throw new Error("Download failed: response had no body to stream.");

  const declared = Number(res.headers.get("content-length")) || undefined;
  const name = fileNameFor(url);
  const tmp = `${name}.part`;
  const handle = await d.getFileHandle(tmp, { create: true });
  const out = await handle.createWritable();

  let written = 0;
  try {
    const reader = res.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      await out.write(value);
      written += value.byteLength;
      onProgress?.(written, declared);
    }
    await out.close();
  } catch (e) {
    await out.abort().catch(() => {});
    await d.removeEntry(tmp).catch(() => {});
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
  return { url, bytes: written, savedAt: Date.now() };
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
    if (h.kind !== "file" || name.endsWith(".part")) continue;
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

/** Bytes stored by this origin and the ceiling the browser will allow, when it says. */
export async function quota(): Promise<{ usage?: number; quota?: number }> {
  if (typeof navigator === "undefined" || !navigator.storage?.estimate) return {};
  return navigator.storage.estimate();
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
