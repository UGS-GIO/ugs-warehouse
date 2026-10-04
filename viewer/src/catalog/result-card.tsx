// The shared result card + list row for Discover. The whole card is a single <a> (keyboard-focusable,
// cmd/middle-click opens a new tab) whose plain left-click the caller intercepts for in-app nav; the
// caller passes the mouse handlers for map↔card hover sync.
//
// A publication card leads with what tells it apart from its neighbors (the quadrangle, the edition
// year), then what kind of work and where, then one line of series ID · year · scale · author. Many
// titles share their first 30 characters ("Photogeologic map of the Moab-1… quadrangle"), so the
// title in title order is the worst way to scan them. The full title is the card's tooltip.
import type { ReactNode } from "react";

import { Link } from "@tanstack/react-router";

import type { ItemRef } from "./browse";
import { cardText } from "./card-text";
import { hasGeometry, seriesLabel } from "./item-view";
import { thumbnailAsset } from "@/stac";
import { C } from "@/ui/ui";
import { useDataSaver } from "@/lib/data-saver";

export type Density = "comfortable" | "compact";

// A <Link> descriptor, not an <a>. The router builds the href (basepath applied), does SPA nav on a
// plain click and leaves modifier/middle-click to the browser — all of which this used to hand-roll.
export type LinkAttrs = {
  to: "/catalog" | "/discover";
  search: Record<string, unknown> | ((prev: Record<string, unknown>) => Record<string, unknown>);
  "data-href": string;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
};

// addSlot is an optional caller-supplied control (the "+ Add to map" button on Discover). It's a
// slot, not a bool, so this card stays presentational and free of the @/app graph. `idMatch` marks
// the item a typed series id names.
type CardProps = { it: ItemRef; density: Density; on: boolean; link: LinkAttrs; addSlot?: ReactNode; idMatch?: boolean };

/** Where a result card points. Wide opens the Discover drawer and must PRESERVE the filter keys —
 *  a plain object replaces the search and silently clears them. Narrow leaves for the full page,
 *  where the Discover filters do not apply, so there it replaces on purpose. */
export const itemLink = (it: ItemRef, wide: boolean): LinkAttrs => {
  const sel = { c: it.collId, i: it.href.split("/").slice(-2)[0] };
  return {
    to: wide ? "/discover" : "/catalog",
    search: wide ? (prev: Record<string, unknown>) => ({ ...prev, ...sel }) : sel,
    "data-href": it.href,
  };
};

const FOCUS_RING = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function Badges({ interim, idMatch }: { interim: boolean; idMatch?: boolean }) {
  if (!interim && !idMatch) return null;
  return (
    <span className="flex shrink-0 gap-1">
      {idMatch && <span className="rounded bg-emerald-100 px-1.5 text-[11px] font-semibold text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100">Series ID match</span>}
      {interim && <span className="rounded bg-amber-100 px-1.5 text-[11px] font-semibold text-amber-900 dark:bg-amber-900/40 dark:text-amber-100">Interim</span>}
    </span>
  );
}

// series id · year · scale · author, the id set apart so a run of near-identical cards reads by it.
// A layer has no such line; it keeps its own id (seriesLabel), as before.
function Meta({ it, meta }: { it: ItemRef; meta: string[] }) {
  if (!meta.length) {
    const sid = seriesLabel(it);
    return sid ? <div className="truncate font-mono text-[11px] text-muted-foreground" title={sid}>{sid}</div> : null;
  }
  const [id, ...rest] = meta;
  // One block of inline text, so a narrow card ends in "…" rather than clipping mid-word.
  return (
    <div className="truncate text-[11px] text-muted-foreground" title={meta.join(" · ")}>
      <span className="font-mono font-semibold text-foreground">{id}</span>
      {rest.length ? ` · ${rest.join(" · ")}` : ""}
    </div>
  );
}

// Muted, NON-anchor format chips — the card is itself an <a>, so it must contain no nested anchors.
// Thumbnails/images are already the card image, so they're dropped.
const formatBadges = (it: ItemRef) =>
  Object.entries(it.data?.assets ?? {})
    .filter(([, a]) => !a.roles?.includes("thumbnail") && !a.type?.startsWith("image/"))
    .slice(0, 4)
    .map(([k, a]) => <span key={k} className={C.badge}>{a.title ?? k}</span>);

// Gallery card: thumbnail beside the heading, kind and place, and the id line (+ format chips when
// roomy).
export function ResultCard({ it, density, on, link, addSlot, idMatch }: CardProps) {
  const saver = useDataSaver();   // data saver: no images the person did not ask for
  const th = thumbnailAsset(it.data);
  const c = cardText(it);
  const compact = density === "compact";
  // The border/hover live on the outer container so the add control can sit in a footer BELOW the
  // link: a button can't be a valid descendant of the card's <a>.
  return (
    <div className={`flex flex-col overflow-hidden rounded-lg border bg-card transition hover:border-primary hover:shadow-sm ${on ? "border-primary ring-1 ring-primary" : "border-border"}`}>
      <Link {...link} title={c.full ?? c.heading}
        className={`flex flex-1 cursor-pointer gap-3 p-3 text-inherit no-underline ${FOCUS_RING}`}>
        <div className={`${compact ? "h-12 w-12" : "h-20 w-20"} flex shrink-0 items-center justify-center overflow-hidden rounded-md border border-border bg-muted`}>
          {/* No placeholder text: an empty slot reads as "no thumbnail" more honestly than a clipped id. */}
          {th && !saver && <img src={th.href} alt="" loading="lazy" className="h-full w-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-1.5">
            <p className={`min-w-0 flex-1 font-semibold leading-tight text-foreground ${compact ? "line-clamp-1" : "line-clamp-2"} text-sm`}>{c.heading}</p>
            {hasGeometry(it) && <span className="shrink-0 text-[10px] font-medium text-primary"><span aria-hidden>◆</span> map</span>}
          </div>
          {c.sub && <div className="truncate text-xs text-muted-foreground" title={c.sub}>{c.sub}</div>}
          <Meta it={it} meta={c.meta} />
          {(c.interim || idMatch) && <div className="mt-1"><Badges interim={c.interim} idMatch={idMatch} /></div>}
          {!compact && <div className="mt-1">{formatBadges(it)}</div>}
        </div>
      </Link>
      {addSlot && <div className="flex justify-end border-t border-border/60 px-2 py-1.5">{addSlot}</div>}
    </div>
  );
}

// List row: one dense line for scanning many at once. The heading leads, kind and place follow
// muted, then the id (and year · scale · author when roomy).
export function ResultRow({ it, density, on, link, addSlot, idMatch }: CardProps) {
  const c = cardText(it);
  const compact = density === "compact";
  const [id, ...rest] = c.meta;
  const sid = id ?? seriesLabel(it);
  return (
    <li className={`flex items-center gap-1 ${on ? "bg-primary/10" : "hover:bg-hover"}`}>
      <Link {...link} title={c.full ?? c.heading}
        className={`flex min-w-0 flex-1 cursor-pointer items-baseline gap-2 px-3 text-inherit no-underline ${compact ? "py-1" : "py-2"} ${FOCUS_RING}`}>
        <span className="min-w-0 truncate text-sm">
          <span className="text-foreground">{c.heading}</span>
          {c.sub && <span className="text-muted-foreground"> · {c.sub}</span>}
        </span>
        {sid && <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{sid}</span>}
        {!compact && rest.length > 0 && <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">{rest.join(" · ")}</span>}
        <Badges interim={c.interim} idMatch={idMatch} />
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
