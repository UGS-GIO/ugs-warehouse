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

/** A JSON file's contents, or null when it is missing or not JSON. */
export async function readJson(dir: FileSystemDirectoryHandle, name: string): Promise<unknown> {
  try {
    const text = await (await (await dir.getFileHandle(name)).getFile()).text();
    const value: unknown = JSON.parse(text);
    return value;
  } catch {
    return null;
  }
}
