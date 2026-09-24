// Type guards for what offline storage hands back untyped: directory entries and saved JSON.
// Checked, never asserted: a file written by an older build, or damaged, reads as absent.
// Worker-safe (no DOM beyond storage types), so the service worker shares them.

export type Bbox = [number, number, number, number];

export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export const isFile = (h: FileSystemHandle): h is FileSystemFileHandle => h.kind === "file";

export const isDirectory = (h: FileSystemHandle): h is FileSystemDirectoryHandle => h.kind === "directory";

export const isBbox = (v: unknown): v is Bbox =>
  Array.isArray(v) && v.length === 4 && v.every((n) => typeof n === "number" && Number.isFinite(n));

export const optionalString = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);

export const optionalNumber = (v: unknown): number | undefined =>
  typeof v === "number" && Number.isFinite(v) ? v : undefined;

/** The saved areas in a record, dropping any entry that is not a bbox. */
export const bboxesOf = (v: unknown): Bbox[] => (Array.isArray(v) ? v.filter(isBbox) : []);

/** The names in a folder, read once. */
export async function namesIn(dir: FileSystemDirectoryHandle): Promise<Set<string>> {
  const out = new Set<string>();
  for await (const [name] of dir) out.add(name);
  return out;
}

/** How many files a saved folder holds, and their bytes, not counting its meta.json. */
export async function folderSize(dir: FileSystemDirectoryHandle): Promise<{ files: number; bytes: number }> {
  let files = 0;
  let bytes = 0;
  for await (const [name, h] of dir) {
    if (name === "meta.json" || !isFile(h)) continue;
    files++;
    bytes += (await h.getFile()).size;
  }
  return { files, bytes };
}

/** What reading a folder's files needs of it. */
type Readable = { getFileHandle(name: string): Promise<{ getFile(): Promise<File> }> };

/** A JSON file's contents, or null when it is missing or not JSON. */
export async function readJson(dir: Readable, name: string): Promise<unknown> {
  try {
    const text = await (await (await dir.getFileHandle(name)).getFile()).text();
    const value: unknown = JSON.parse(text);
    return value;
  } catch {
    return null;
  }
}


// ---- files saved by area: a folder per file, holding a folder per version of it ----
//
// An update saves the new version beside the copy it replaces, which keeps working until the new
// one is finished, and parts of two versions never share a folder.

export const versionKey = (version: string | undefined) => encodeURIComponent(version ?? "-");

/** A file's finished version (the newest meta.json among its version folders), or null. */
export async function finished(parent: FileSystemDirectoryHandle):
  Promise<{ dir: FileSystemDirectoryHandle; meta: Record<string, unknown> } | null> {
  let best: { dir: FileSystemDirectoryHandle; meta: Record<string, unknown> } | null = null;
  for await (const [, h] of parent) {
    if (!isDirectory(h)) continue;
    const meta = await readJson(h, "meta.json");
    if (isRecord(meta) && (!best || (optionalNumber(meta.savedAt) ?? 0) >= (optionalNumber(best.meta.savedAt) ?? 0))) {
      best = { dir: h, meta };
    }
  }
  return best;
}

/**
 * Clear out a file's folder: everything but version `keep` once that version is finished, or, with
 * `keep` null, the versions no save finished (a save removed from the queue, or one that failed).
 */
export async function prune(parent: FileSystemDirectoryHandle, keep: string | null): Promise<void> {
  const drop: string[] = [];
  for await (const [name, h] of parent) {
    if (keep !== null ? name !== keep : !isDirectory(h) || !isRecord(await readJson(h, "meta.json"))) drop.push(name);
  }
  for (const name of drop) await parent.removeEntry(name, { recursive: true }).catch(() => {});
}
