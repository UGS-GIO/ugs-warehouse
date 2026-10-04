// Every TanStack Query key in one place. Written out by hand at ~20 call sites, the keys drifted
// from the invalidations that had to match them: `comments-panel` threads a target into a five-part
// key while `browse` invalidates a two-part one, and that only works because prefix matching happens
// to cover it. Here the prefix relationships are visible, so an invalidation can be checked instead
// of remembered.
import type { CommentTarget } from "@/review/comments";

const comments = {
  /** Every comment, for the review dashboard. `report` extends it, so invalidating this covers both. */
  all: ["comments-all"] as const,
  report: ["comments-all", "report"] as const,
  /** One item's comments — the PREFIX of every per-row/column thread on it. */
  item: (itemId: string) => ["comments", itemId] as const,
  thread: (itemId: string, target?: CommentTarget) =>
    [...comments.item(itemId), target?.kind ?? "item", target?.rowVal ?? null,
     target?.column ?? null] as const,
};

export const qk = {
  // Catalog + styling
  stac: (url?: string) => ["stac", url] as const,
  index: (href: string) => ["index", href] as const,
  styleLayers: (styleUrl?: string) => ["gl-style-layers", styleUrl] as const,
  stylesManifest: (url?: string) => ["styles-manifest", url] as const,

  // Offline store (OPFS): one key for the whole stored set, so a download or delete refreshes
  // every control at once.
  basemapStyle: (id: string) => ["basemap-style", id] as const,
  basemapIndex: ["basemap-index"] as const,

  // Map + raster
  cogBbox: (href: string) => ["cog-bbox", href] as const,
  cogProtocol: ["cog-protocol"] as const,
  colormapSprite: ["colormap-sprite"] as const,
  zarrSource: (href: string, variable: string) => ["zarr-source", href, variable] as const,

  // Tabular data (duckdb-wasm over parquet)
  parquetTypes: (href: string) => ["parquet-types", href] as const,
  parquetPage: (href: string, parts: readonly unknown[]) => ["parquet-page", href, ...parts],
  featureRelated: (href: string, childField: string, value: string) =>
    ["feature-card-related", href, childField, value] as const,

  // Item panels
  photoGallery: (href: string, page: number) => ["ucrc-photo-gallery", href, page] as const,
  textPreview: (href: string) => ["text-preview", href] as const,
  threeDColors: (itemId: unknown) => ["3d-colors", itemId] as const,

  // Search
  articleCorpus: (url: string) => ["article-corpus", url] as const,
  pubFts: (q: string) => ["pub-fts", q] as const,
  /** Place-name suggestions for typed text (the UGRC locator). */
  placeSuggest: (text: string) => ["place-suggest", text] as const,

  // Review deploy
  comments,
  whoami: ["whoami"] as const,
  reviewers: ["reviewers"] as const,
  itemStatus: ["item-status"] as const,
  notifications: ["notifications"] as const,
};
