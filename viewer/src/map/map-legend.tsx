// Legend for the active overlays, shown in the drawer under the layer list — the panel that already
// answers "what is on" is where "what does it mean" belongs, and it costs the map no pixels.
//
// Derived from the same bound style the map draws with (`legendFromStyle`), so it can't drift; a
// layer with no bound style draws in its fallback colour, which is just as much a key.
import { legendFromStyle } from "./legend";

export type LegendLayer = { id: string; title: string; color: string; styleLayers?: Record<string, unknown>[] };

type Section = { id: string; title: string; field?: string; uniform?: boolean; entries: { label: string; color: string }[] };

function sectionOf({ id, title, color, styleLayers }: LegendLayer): Section | null {
  const derived = styleLayers ? legendFromStyle(styleLayers) : null;
  // Uniform symbology derives one blank-labelled swatch — name it with the layer instead.
  if (derived?.entries.length) {
    return derived.uniform
      ? { id, title, uniform: true, entries: [{ label: title, color: derived.entries[0].color }] }
      : { id, title, field: derived.field, entries: derived.entries };
  }
  return styleLayers ? null : { id, title, uniform: true, entries: [{ label: title, color }] };
}

const Swatch = ({ color }: { color: string }) => (
  <span className="inline-block h-3 w-3 shrink-0 rounded-sm border border-border" style={{ background: color }} />
);

export function MapLegend({ layers }: { layers: LegendLayer[] }) {
  const sections = layers.map(sectionOf).filter((s): s is Section => s !== null);
  if (!sections.length) return null;
  return (
    <div className="mb-3 border-b border-border pb-2 text-sm">
      <div className="mb-1.5 font-semibold uppercase tracking-wider text-muted-foreground">Legend</div>
      <div className="flex flex-col gap-2">
        {sections.map((s) => (
          <div key={s.id}>
            {/* Uniform symbology has nothing to enumerate: the swatch IS the layer, so it rides on
                the title instead of repeating it as a one-row list. */}
            <div className="flex items-center gap-1.5 font-medium text-foreground" title={s.title}>
              {s.uniform && <Swatch color={s.entries[0].color} />}
              <span className="truncate">{s.title}</span>
            </div>
            {s.field && <div className="mb-0.5 text-sm uppercase tracking-wide text-muted-foreground">{s.field}</div>}
            {!s.uniform && (
              <div className="flex flex-col gap-0.5">
                {s.entries.map((e, i) => (
                  <span key={i} className="flex items-center gap-1.5 text-foreground">
                    <Swatch color={e.color} />
                    <span className="truncate" title={e.label}>{e.label}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
