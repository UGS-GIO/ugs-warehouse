// A range filter for the facet rail: a histogram of what's there, a two-thumb slider over it, and
// From / To boxes for an exact value (dragging to one is fiddly, and some people can't drag).
import { useState } from "react";

import { UiSlider } from "@/ui/slider";
import { RailSection } from "./rail-section";

export type RangeBin = { from: number; to: number; n: number; tip: string };

export function RangeFacet({ label, summary, min, max, value, bins, onCommit, toText, fromText, labels, note,
  defaultOpen = true }: {
  label: string;
  summary: string;                         // "Any", or the range in words
  min: number;
  max: number;
  value: [number, number];
  bins: RangeBin[];
  onCommit: (lo: number, hi: number, replace?: boolean) => void;  // replace: don't add a Back step
  toText: (v: number) => string;
  fromText: (s: string) => number | null;  // a typed value → slider units, or null when unreadable
  labels: [string, string];                // the thumbs' and boxes' accessible names
  note?: string;                           // e.g. "15 undated items hidden"
  defaultOpen?: boolean;
}) {
  const [lo, hi] = value;
  // A drag or a typed box belongs to the range it started from; a new range replaces it.
  const at = `${lo},${hi}`;
  const [drag, setDrag] = useState({ at, v: value });
  const [typed, setTyped] = useState({ at, t: [null, null] as [string | null, string | null] });
  const live = drag.at === at ? drag.v : value;
  const text = typed.at === at ? typed.t : [null, null];
  const setText = (t: [string | null, string | null]) => setTyped({ at, t });

  const peak = Math.max(1, ...bins.map((b) => b.n));
  const shown = (i: 0 | 1) => text[i] ?? ((i === 0 ? live[0] === min : live[1] === max) ? "" : toText(live[i]));
  const commitText = (i: 0 | 1) => {
    const raw = text[i];
    if (raw === null) return;
    const v = raw.trim() === "" ? (i === 0 ? min : max) : fromText(raw);
    const reset = () => setText(i === 0 ? [null, text[1]] : [text[0], null]);
    if (v === null) { reset(); return; }   // unreadable: put this box back, leave the other alone
    const c = Math.min(max, Math.max(min, v));
    onCommit(i === 0 ? Math.min(c, hi) : lo, i === 1 ? Math.max(c, lo) : hi);
    reset();   // show the value it resolved to, even when that's the range it already had
  };

  return (
    <RailSection label={label} summary={summary} defaultOpen={defaultOpen || summary !== "Any"}>
      <div className="px-1 pt-2">
        <div className="flex h-10 items-end gap-px px-2" aria-hidden>
          {bins.map((b) => {
            const inRange = b.to >= live[0] && b.from <= live[1];
            return (
              <div key={b.from} title={b.tip} className={`flex-1 rounded-t-sm ${b.n === 0 ? "bg-border"
                : inRange ? "bg-primary/60" : "bg-muted-foreground/25"}`}
                style={{ height: b.n ? `${Math.max(8, (b.n / peak) * 100)}%` : "1px" }} />
            );
          })}
        </div>
        <UiSlider value={live} min={min} max={max} labels={labels} valueText={toText}
          onValueChange={(v) => setDrag({ at, v })}
          // Arrow keys commit every step; those replace the history entry instead of piling up.
          onValueCommitted={([a, b], reason) => onCommit(a, b, reason === "keyboard")} />
        <div className="mt-2 grid grid-cols-2 gap-2">
          {([0, 1] as const).map((i) => (
            <label key={i} className="flex flex-col gap-0.5 text-2xs text-muted-foreground">
              {i === 0 ? "From" : "To"}
              <span className="flex items-center rounded-md border border-input bg-card px-2 focus-within:border-primary">
                <input inputMode="numeric" placeholder="Any" aria-label={labels[i]} value={shown(i)}
                  onChange={(e) => setText(i === 0 ? [e.target.value, text[1]] : [text[0], e.target.value])}
                  onBlur={() => commitText(i)}
                  onKeyDown={(e) => { if (e.key === "Enter") commitText(i); }}
                  className="h-8 w-full min-w-0 bg-transparent text-sm tabular-nums text-foreground outline-none placeholder:text-muted-foreground" />
              </span>
            </label>
          ))}
        </div>
        {note && <p className="mt-1.5 text-2xs text-muted-foreground">{note}</p>}
      </div>
    </RailSection>
  );
}
