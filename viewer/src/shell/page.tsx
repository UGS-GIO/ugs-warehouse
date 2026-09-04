// One page container for the whole app: data pages fill the window, prose clamps for line length.

type Width = "full" | "prose";

// Padding grows with the viewport instead of the content shrinking away from it.
const PAD = "px-4 py-6 sm:px-6 lg:px-10";

export function Page({ width = "full", className = "", children }: {
  width?: Width; className?: string; children: React.ReactNode;
}) {
  const clamp = width === "prose" ? "mx-auto max-w-[75ch]" : "";
  return <div className={`w-full ${PAD} ${clamp} ${className}`}>{children}</div>;
}

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
