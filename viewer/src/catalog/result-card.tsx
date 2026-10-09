// The result card + list row for Discover. The whole card is a single <a> (keyboard-focusable,
// cmd/middle-click opens a new tab) whose plain left-click the caller intercepts for in-app nav; a
// caller that wants map↔card hover sync passes the mouse handlers.
import type { ReactNode } from "react";

import { Link } from "@tanstack/react-router";

import type { ItemRef } from "./browse";
import { collectionLabel, dateOf, hasGeometry, seriesLabel, title, typeOf } from "./item-view";
import { thumbnailAsset } from "@/stac";
import { C } from "@/ui/ui";
import { useDataSaver } from "@/lib/data-saver";

export type Density = "comfortable" | "compact";

// A <Link> descriptor, not an <a>. The router builds the href (basepath applied), does SPA nav on a
// plain click and leaves modifier/middle-click to the browser — all of which this used to hand-roll.
export type LinkAttrs = {
  to: "/catalog" | "/";
  search: Record<string, unknown> | ((prev: Record<string, unknown>) => Record<string, unknown>);
  "data-href": string;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
};

// addSlot is an optional caller-supplied control (the "+ Add to map" button on Discover). It's a
// slot, not a bool, so this card stays presentational and free of the @/app graph — the same reason
// the mouse handlers are passed in rather than wired here.
type CardProps = { it: ItemRef; density: Density; on: boolean; link: LinkAttrs; addSlot?: ReactNode };

/** Where a result card points. Wide opens the Discover drawer and must PRESERVE the filter keys —
 *  a plain object replaces the search and silently clears them. Narrow leaves for the full page,
 *  where the Discover filters do not apply, so there it replaces on purpose. */
export const itemLink = (it: ItemRef, wide: boolean): LinkAttrs => {
  const sel = { c: it.collId, i: it.href.split("/").slice(-2)[0] };
  return {
    to: wide ? "/" : "/catalog",
    search: wide ? (prev: Record<string, unknown>) => ({ ...prev, ...sel }) : sel,
    "data-href": it.href,
  };
};

// collection · type · date, as a muted meta line (reference parity — text, not a badge wall).
const metaLine = (it: ItemRef) => [collectionLabel(it.collId), typeOf(it), dateOf(it)].filter(Boolean).join(" · ");
// Muted, NON-anchor format chips — the card is itself an <a>, so it must contain no nested anchors.
// Thumbnails/images are already the card image, so they're dropped.
const formatBadges = (it: ItemRef) =>
  Object.entries(it.data?.assets ?? {})
    .filter(([, a]) => !a.roles?.includes("thumbnail") && !a.type?.startsWith("image/"))
    .slice(0, 4)
    .map(([k, a]) => <span key={k} className={C.badge}>{a.title ?? k}</span>);

const FOCUS_RING = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

// Gallery card — the centerpiece: thumbnail + series + title + meta (+ format chips when roomy).
export function ResultCard({ it, density, on, link, addSlot }: CardProps) {
  const saver = useDataSaver();   // data saver: no images the person did not ask for
  const th = thumbnailAsset(it.data);
  const sid = seriesLabel(it);
  const compact = density === "compact";
  // The border/hover live on the outer container so the add control can sit in a footer BELOW the
  // link — a button can't be a valid descendant of the card's <a>, and no overlaid corner clears both
  // the title (top) and the format badges (bottom) on a narrow grid card.
  return (
    <div className={`flex flex-col overflow-hidden rounded-lg border bg-card transition hover:border-primary hover:shadow-sm ${on ? "border-primary ring-1 ring-primary" : "border-border"}`}>
      <Link {...link}
        className={`flex flex-1 cursor-pointer gap-3 p-3 text-inherit no-underline ${FOCUS_RING}`}>
        <div className={`${compact ? "h-12 w-12" : "h-20 w-20"} flex shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-muted`}>
          {/* No placeholder text: at 48-80px the id just clips ("geolmap_strat_columns_geol…"), so an
              empty slot reads as "no thumbnail" more honestly than a truncated machine name. */}
          {th && !saver && <img src={th.href} alt="" loading="lazy" className="h-full w-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1.5">
            <p className={`min-w-0 flex-1 font-semibold leading-tight text-foreground ${compact ? "line-clamp-1" : "line-clamp-2"} text-sm`}>{title(it)}</p>
            {hasGeometry(it) && <span className="shrink-0 text-[10px] font-medium text-primary"><span aria-hidden>◆</span> map</span>}
          </div>
          {sid && <div className="truncate font-mono text-[11px] text-muted-foreground" title={sid}>{sid}</div>}
          <div className="mt-1 truncate text-xs text-muted-foreground" title={metaLine(it)}>{metaLine(it)}</div>
          {!compact && <div className="mt-1">{formatBadges(it)}</div>}
        </div>
      </Link>
      {addSlot && <div className="flex justify-end border-t border-border/60 px-2 py-1.5">{addSlot}</div>}
    </div>
  );
}

// List row — one dense line for scanning many at once.
export function ResultRow({ it, density, on, link, addSlot }: CardProps) {
  const compact = density === "compact";
  const sid = seriesLabel(it);
  return (
    <li className={`flex items-center gap-1 ${on ? "bg-primary/10" : "hover:bg-hover"}`}>
      <Link {...link}
        className={`flex min-w-0 flex-1 cursor-pointer items-baseline gap-2 px-3 text-inherit no-underline ${compact ? "py-1" : "py-2"} ${FOCUS_RING}`}>
        <span className="truncate text-sm text-foreground" title={title(it)}>{title(it)}</span>
        {sid && <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{sid}</span>}
        {!compact && <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{collectionLabel(it.collId)}</span>}
        {hasGeometry(it) && (
          <span className="ml-auto shrink-0 text-primary">
            <span aria-hidden className="text-[10px]">◆</span><span className="sr-only">on the map</span>
          </span>
        )}
      </Link>
      {addSlot && <div className="shrink-0 pr-2">{addSlot}</div>}
    </li>
  );
}
