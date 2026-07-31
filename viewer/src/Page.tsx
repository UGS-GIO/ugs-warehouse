// One page container for the whole app.
//
// Before this, every route picked its own width — 768 / 920 / 1100 / 1400 / 1700, each centred —
// so nothing lined up between pages and a 1920 screen spent ~27% of its width on empty gutters.
// Data pages now fill the window; only prose clamps, because line length is a real readability
// limit rather than a habit.

type Width = "full" | "prose";

// Padding grows with the viewport instead of the content shrinking away from it.
const PAD = "px-4 py-6 sm:px-6 lg:px-10";

export function Page({ width = "full", className = "", children }: {
  width?: Width; className?: string; children: React.ReactNode;
}) {
  const clamp = width === "prose" ? "mx-auto max-w-[75ch]" : "";
  return <div className={`w-full ${PAD} ${clamp} ${className}`}>{children}</div>;
}

// Card grids size themselves to the window: 3 across at 1280, 5 at 1920, 1 on a phone — without
// hardcoding breakpoints per grid. `auto-fill` (not `auto-fit`) so a short row keeps card width
// instead of stretching two cards across the whole screen.
export const CARD_GRID = "grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(320px,1fr))]";

// Type scale. These were all within ~2px of each other, so nothing led the eye.
export const T = {
  pageTitle: "text-2xl font-semibold tracking-tight",
  section: "text-lg font-semibold tracking-tight",
  cardTitle: "text-base font-semibold leading-tight",
  meta: "text-xs text-muted-foreground",
};
