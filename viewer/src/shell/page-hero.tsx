// Title band for the content pages (Guide, the catalog landing), laid out like the
// soil-water app's docs hero: eyebrow, title, lead, over a UGS photo with a vertical scrim.
//
// The scrim is what keeps the text readable: the type colour is fixed and the photo isn't, so a
// gradient guarantees contrast rather than hoping for it, and `drop-shadow` covers bright patches.
import type { ReactNode } from "react";

import heroImg from "@/assets/fantasy-canyon.jpg";
import { useDataSaver } from "@/lib/data-saver";

export function PageHero({ eyebrow, title, lead, children }: {
  eyebrow?: string;       // section, NOT the agency — the state band above already names it
  title: string;
  lead?: string;
  children?: ReactNode;   // chips, counts, a filter row — sits under the lead
}) {
  const saver = useDataSaver();   // data saver: no images the person did not ask for
  return (
    <header className="relative border-b border-border">
      {!saver && <img src={heroImg} alt="" aria-hidden className="absolute inset-0 h-full w-full object-cover" />}
      <div aria-hidden className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/65 to-black/40" />
      <div className="relative w-full px-4 py-16 text-white sm:px-6 lg:px-10">
        {eyebrow && (
          <p className="text-sm font-semibold uppercase tracking-wider text-white/90 drop-shadow-md">
            {eyebrow}
          </p>
        )}
        <h1 className="font-display text-3xl tracking-tight drop-shadow-md sm:text-4xl">{title}</h1>
        {lead && <p className="mt-3 max-w-[75ch] text-base drop-shadow-md">{lead}</p>}
        {children && <div className="mt-4">{children}</div>}
      </div>
    </header>
  );
}
