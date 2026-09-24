// The OPFS filename for an artifact URL, in its own module because both the app and the service
// worker need it and the worker must not pull in the rest of the store.
//
// The URL IS the filename (percent-encoded, so no "/" survives), so a directory listing is the
// manifest and there is no side table to keep in step.

/** OPFS filename for an artifact URL. Reversible, so a listing needs no side table. */
export const fileNameFor = (url: string): string => encodeURIComponent(url);

/** The URL a stored file came from. Inverse of `fileNameFor`. */
export const urlFromFileName = (name: string): string => decodeURIComponent(name);

/**
 * Which version of a published file a response came from: its ETag, else its Last-Modified. Parts
 * of a file saved by area are only valid against the version they were cut from.
 */
export const versionOf = (h: Headers): string | undefined =>
  h.get("etag") ?? h.get("last-modified") ?? undefined;

/** The current version of a published file, from a HEAD request. "no-store" also tells the service
 *  worker to let it through to the network rather than answer from a saved copy. */
export async function currentVersion(url: string): Promise<string | undefined> {
  const r = await fetch(url, { method: "HEAD", cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status}`);
  return versionOf(r.headers);
}

/** Thrown when a file changes while a save planned against it is running: its offsets are void. */
export class FileChangedError extends Error {
  constructor(url: string) {
    super(`${url.split("/").pop()} was republished during the save.`);
    this.name = "FileChangedError";
  }
}
