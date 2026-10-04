// What a Discover result card says, kept apart from the card component so it is testable without
// React. See result-card.tsx for why a publication card leads with part of its title.
import type { ItemRef } from "./browse";
import { collectionLabel, dateOf, firstAuthor, fmtDate, isPublication, itemIdOf, scaleDenominator, title,
  typeOf } from "./item-view";
import { isInterim, titleParts } from "./title-parts";

export type CardText = {
  heading: string;   // the line to scan by
  sub: string;       // what kind of work, and where
  meta: string[];    // series id, year, scale, author (publications); empty for a layer
  full?: string;     // the whole title, when the heading is only part of it
  interim: boolean;
};

/** What a card says. Publications are split (titleParts); layers keep their title and the
 *  collection · type · date line they had. */
export function cardText(it: ItemRef): CardText {
  const t = title(it);
  if (!isPublication(it)) {
    return { heading: t, sub: [...new Set([collectionLabel(it.collId), typeOf(it), dateOf(it)].filter(Boolean))].join(" · "),
      meta: [], interim: false };
  }
  const parts = titleParts(t);
  const d = scaleDenominator(it);
  return {
    heading: parts?.lead ?? t,
    sub: parts ? [parts.kind, parts.where].filter(Boolean).join(" · ") : typeOf(it),
    meta: [itemIdOf(it), fmtDate(dateOf(it)) || "Undated", d ? `1:${d.toLocaleString("en-US")}` : "", firstAuthor(it)]
      .filter(Boolean),
    full: parts ? t : undefined,
    interim: isInterim(t),
  };
}
