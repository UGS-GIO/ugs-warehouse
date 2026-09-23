// The OPFS filename for an artifact URL, in its own module because both the app and the service
// worker need it and the worker must not pull in the rest of the store.
//
// The URL IS the filename (percent-encoded, so no "/" survives), so a directory listing is the
// manifest and there is no side table to keep in step.

/** OPFS filename for an artifact URL. Reversible, so a listing needs no side table. */
export const fileNameFor = (url: string): string => encodeURIComponent(url);

/** The URL a stored file came from. Inverse of `fileNameFor`. */
export const urlFromFileName = (name: string): string => decodeURIComponent(name);
