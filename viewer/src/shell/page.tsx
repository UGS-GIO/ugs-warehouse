// auto-fill, not auto-fit: a short row keeps card width instead of stretching to fill.
// min(320px,100%) not a bare 320px: a px floor cannot shrink, so the track outgrows any container
// narrower than it and the whole subtree inherits that width.
export const CARD_GRID = "grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(min(320px,100%),1fr))]";

// Type scale. These were all within ~2px of each other, so nothing led the eye.
export const T = {
  pageTitle: "text-2xl font-semibold tracking-tight",
  section: "text-lg font-semibold tracking-tight",
  cardTitle: "text-base font-semibold leading-tight",
  meta: "text-xs text-muted-foreground",
};
