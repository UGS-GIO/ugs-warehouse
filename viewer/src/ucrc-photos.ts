// UCRC core-photo CDN URL helpers. Photos live on the UCRC assets CDN; the parquet's `storage_path`
// (photos/<uwi>/box_N/<file>.jpg) resolves to the full image, and photos/_thumbs/200/… to a 200px
// thumbnail — same scheme as ugs-map-viewer's box-photos popup. Pure (no React) so it's unit-tested.
export const UCRC_CDN = "https://ucrc-assets.geology.utah.gov";

export const encodePath = (p: string) => p.split("/").map(encodeURIComponent).join("/");

export const thumbPath = (sp: string) =>
  sp.startsWith("photos/") ? `photos/_thumbs/200/${sp.slice("photos/".length)}` : `_thumbs/200/${sp}`;

export const fullUrl = (sp: string) => `${UCRC_CDN}/${encodePath(sp)}`;
export const thumbUrl = (sp: string) => `${UCRC_CDN}/${encodePath(thumbPath(sp))}`;
