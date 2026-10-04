// The shared result card + list row for Discover. The whole card is a single <a> (keyboard-focusable,
// cmd/middle-click opens a new tab) whose plain left-click the caller intercepts for in-app nav; the
// caller passes the mouse handlers for map↔card hover sync.
//
// A publication card leads with what tells it apart from its neighbors (the quadrangle, the edition
// year), then what kind of work and where, then one line of series ID · year · scale · author. Many
// titles share their first 30 characters ("Photogeologic map of the Moab-1… quadrangle"), so the
// title in title order is the worst way to scan them. The full title stays on the card.
import type { ReactNode } from "react";

import { Link } from "@tanstack/react-router";

import type { ItemRef } from "./browse";
import { cardText } from "./card-text";
import { hasGeometry, seriesLabel } from "./item-view";
import { thumbnailAsset } from "@/stac";
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
function Meta({ meta, it }: { meta: string[]; it: ItemRef }) {
  if (!meta.length) return null;
  const [id, ...rest] = meta;
  return (
    <div className="flex flex-wrap items-baseline gap-x-2.5 text-xs text-muted-foreground">
      <span className="font-mono font-semibold text-foreground">{id}</span>
      {rest.map((m) => <span key={m}>{m}</span>)}
      <OnMap it={it} />
    </div>
  );
}

function OnMap({ it }: { it: ItemRef }) {
  return hasGeometry(it) ? <span className="shrink-0 text-xs text-primary"><span aria-hidden>◆</span> map</span> : null;
}

function Thumb({ it, className }: { it: ItemRef; className: string }) {
  const saver = useDataSaver();   // data saver: no images the person did not ask for
  const th = thumbnailAsset(it.data);
  return (
    <div className={`flex shrink-0 items-center justify-center overflow-hidden bg-muted ${className}`}>
      {/* No placeholder text: an empty slot reads as "no thumbnail" more honestly than a clipped id. */}
      {th && !saver && <img src={th.href} alt="" loading="lazy" className="max-h-full max-w-full object-contain" />}
    </div>
  );
}

// Gallery card. Comfortable: the thumbnail on top, big enough to tell two map sheets apart.
// Compact: a small thumbnail beside the text.
export function ResultCard({ it, density, on, link, addSlot, idMatch }: CardProps) {
  const c = cardText(it);
  const compact = density === "compact";
  const sid = c.meta.length ? undefined : seriesLabel(it);
  // The border/hover live on the outer container so the add control can sit in a footer BELOW the
  // link: a button can't be a valid descendant of the card's <a>.
  return (
    <div className={`flex flex-col overflow-hidden rounded-lg border bg-card transition hover:border-primary hover:shadow-sm ${on ? "border-primary ring-1 ring-primary" : "border-border"}`}>
      <Link {...link} title={c.full ?? c.heading}
        className={`flex flex-1 cursor-pointer text-inherit no-underline ${compact ? "gap-3 p-3" : "flex-col"} ${FOCUS_RING}`}>
        {compact ? <Thumb it={it} className="h-12 w-12 rounded-md border border-border" />
          : <Thumb it={it} className="aspect-[4/3] w-full border-b border-border" />}
        <div className={`min-w-0 flex-1 space-y-0.5 ${compact ? "" : "p-3"}`}>
          <Badges interim={c.interim} idMatch={idMatch} />
          <p className={`font-semibold leading-tight text-foreground ${compact ? "line-clamp-1 text-sm" : "line-clamp-2"}`}>{c.heading}</p>
          {sid && <div className="truncate font-mono text-[11px] text-muted-foreground" title={sid}>{sid}</div>}
          {c.sub && <div className="truncate text-xs text-muted-foreground" title={c.sub}>{c.sub}</div>}
          {c.meta.length ? <Meta meta={c.meta} it={it} /> : <OnMap it={it} />}
        </div>
      </Link>
      {addSlot && <div className="flex justify-end border-t border-border/60 px-2 py-1.5">{addSlot}</div>}
    </div>
  );
}

// List row. Comfortable: thumbnail, the split title and the full one, for comparing a run of
// similar items. Compact: one line per item.
export function ResultRow({ it, density, on, link, addSlot, idMatch }: CardProps) {
  const c = cardText(it);
  const compact = density === "compact";
  return (
    <li className={`flex items-center gap-1 ${on ? "bg-primary/10" : "hover:bg-hover"}`}>
      <Link {...link} title={c.full ?? c.heading}
        className={`flex min-w-0 flex-1 cursor-pointer gap-3 px-3 text-inherit no-underline ${compact ? "items-baseline py-1.5" : "items-start py-2.5"} ${FOCUS_RING}`}>
        {compact ? (
          <>
            <span className="min-w-0 flex-1 truncate text-sm">
              <span className="font-semibold text-foreground">{c.heading}</span>
              {c.sub && <span className="text-muted-foreground"> · {c.sub}</span>}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">{c.meta.slice(0, 3).join(" · ")}</span>
            <Badges interim={c.interim} idMatch={idMatch} />
            {hasGeometry(it) && <span className="shrink-0 text-[10px] text-primary"><span aria-hidden>◆</span><span className="sr-only">on the map</span></span>}
          </>
        ) : (
          <>
            <Thumb it={it} className="h-20 w-20 rounded-md border border-border" />
            <div className="min-w-0 flex-1 space-y-0.5">
              <div className="flex items-start gap-2">
                <p className="line-clamp-2 min-w-0 flex-1 font-semibold leading-tight text-foreground">{c.heading}</p>
                <Badges interim={c.interim} idMatch={idMatch} />
              </div>
              {c.sub && <div className="truncate text-sm text-muted-foreground" title={c.sub}>{c.sub}</div>}
              {c.meta.length ? <Meta meta={c.meta} it={it} /> : <OnMap it={it} />}
              {c.full && <div className="truncate text-[11px] text-muted-foreground">{c.full}</div>}
            </div>
          </>
        )}
      </Link>
      {addSlot && <div className="shrink-0 pr-2">{addSlot}</div>}
    </li>
  );
}
