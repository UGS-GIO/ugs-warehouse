// Legend derived from a bound ugs-styles GL fragment — no separate legend data. Two style shapes:
//   1. one layer, data-driven `-color`: `match` (categorical) or `step` (graduated).
//   2. one layer PER category: a flat `-color` + a `filter` selecting that category (the common
//      ugs-styles shape, e.g. ccus geochemistry / pipelines). Each filtered layer → one entry.
// Falls out of the style for free, so the legend can never drift from the render.

// `values` (optional): the specific category values a grouped legend entry rolls up (e.g. box-type
// Core → BUTTS, SLABS, …), shown under the group label. Each value carries its own colour — which
// for a per-group palette is simply the group's. `label` is the display string when the raw value
// is a shouty managed code ('CORE CHIPS' → 'Core Chips'); the raw value is the fallback.
type Entry = {
  label: string; color: string;
  values?: readonly { value: string; color: string; label?: string }[];
};

// Pull the styled field name out of an input expression: ["get","x"] or ["coalesce",["get","x"],""].
const fieldOf = (input: unknown): string | undefined => {
  if (!Array.isArray(input)) return undefined;
  if (input[0] === "get") return String(input[1]);
  for (const a of input.slice(1)) {
    const f = fieldOf(a);
    if (f) return f;
  }
  return undefined;
};

// The "main" fill color of a layer's paint (skip stroke/outline/halo), only if it's a flat string.
const FLAT_COLOR_KEYS = ["fill-color", "circle-color", "line-color", "icon-color", "text-color"];
const flatColor = (paint: Record<string, unknown>): string | undefined => {
  for (const k of FLAT_COLOR_KEYS) if (typeof paint[k] === "string") return paint[k] as string;
  return undefined;
};

// Label + field from a layer `filter` selecting one category, e.g.
//   ["==", ["get","datatype"], "core analysis"]  ·  ["in", "average", ["get","datatype"]]
//   ["match", ["get","x"], ["a","b"], true, false]  ·  ["all"/"any", <sub>, …]
function labelFromFilter(filter: unknown): { field?: string; label: string } | null {
  if (!Array.isArray(filter)) return null;
  const op = filter[0];
  if (op === "==") {
    const a = filter[1], b = filter[2];
    const lit = !Array.isArray(a) ? a : !Array.isArray(b) ? b : undefined;
    const expr = Array.isArray(a) ? a : Array.isArray(b) ? b : undefined;
    return lit === undefined ? null : { field: fieldOf(expr), label: String(lit) };
  }
  if (op === "in") {
    const needle = filter[1];
    if (!Array.isArray(needle)) return { field: fieldOf(filter[2]), label: String(needle) };
    const vals = filter.slice(2).filter((v) => !Array.isArray(v));   // legacy ["in", field, …vals]
    return vals.length ? { field: fieldOf(needle), label: vals.map(String).join(", ") } : null;
  }
  if (op === "match") {
    const vals = filter[2];
    return { field: fieldOf(filter[1]),
             label: Array.isArray(vals) ? vals.map(String).join(", ") : String(vals) };
  }
  if (op === "all" || op === "any") {
    for (const sub of filter.slice(1)) { const r = labelFromFilter(sub); if (r) return r; }
  }
  return null;
}

// Fallback: one entry per filtered flat-color layer (the per-category-layer style shape). If the
// style has no categories at all (uniform symbology — one flat color, no filter), return a single
// swatch flagged `uniform` so the caller can label it with the dataset name.
function legendFromLayerFilters(
  layers: Array<Record<string, unknown>>,
): { field?: string; entries: Entry[]; uniform?: boolean } | null {
  const entries: Entry[] = [];
  let field: string | undefined;
  for (const layer of layers) {
    const color = flatColor((layer.paint ?? {}) as Record<string, unknown>);
    const lbl = labelFromFilter(layer.filter);
    if (!color || !lbl) continue;   // unfiltered base layers / unparseable filters handled below
    field ??= lbl.field;
    entries.push({ label: lbl.label, color });
  }
  if (entries.length) return { field, entries };
  // Uniform symbology: a single flat color, no categories → one swatch (label filled by caller).
  const uniform = layers.map((l) => flatColor((l.paint ?? {}) as Record<string, unknown>)).find(Boolean);
  return uniform ? { entries: [{ label: "", color: uniform }], uniform: true } : null;
}

export function legendFromStyle(
  layers: Array<Record<string, unknown>>,
): { field?: string; entries: Entry[]; uniform?: boolean } | null {
  for (const layer of layers) {
    const paint = (layer.paint ?? {}) as Record<string, unknown>;
    for (const [key, v] of Object.entries(paint)) {
      if (!key.endsWith("-color") || !Array.isArray(v)) continue;

      if (v[0] === "match") {
        // ["match", input, val, color, …, fallback?]
        const pairs = v.slice(2);
        const hasFallback = pairs.length % 2 === 1;
        const body = hasFallback ? pairs.slice(0, -1) : pairs;
        const entries: Entry[] = [];
        for (let i = 0; i + 1 < body.length; i += 2) {
          entries.push({ label: String(body[i]), color: String(body[i + 1]) });
        }
        if (hasFallback) entries.push({ label: "Other", color: String(pairs[pairs.length - 1]) });
        if (entries.length) return { field: fieldOf(v[1]), entries };
      }

      if (v[0] === "step") {
        // ["step", input, color0, stop1, color1, stop2, color2, …]
        const entries: Entry[] = [{ label: `< ${v[3]}`, color: String(v[2]) }];
        for (let i = 3; i + 1 < v.length; i += 2) {
          entries.push({ label: `≥ ${v[i]}`, color: String(v[i + 1]) });
        }
        if (entries.length) return { field: fieldOf(v[1]), entries };
      }
    }
  }
  // No data-driven paint → the per-category-layer shape (the common ugs-styles authoring).
  return legendFromLayerFilters(layers);
}

// `entries`/`title` override an explicit legend (icon renders carry no derivable paint — e.g.
// wells by-boxtype pie wedges). Otherwise derive from the style layers. `name` (the dataset title)
// labels the single swatch of a uniform-symbology style.
export function Legend({ layers, entries, title, name }: {
  layers?: Array<Record<string, unknown>>; entries?: Entry[]; title?: string; name?: string;
}) {
  const derived = layers ? legendFromStyle(layers) : null;
  const base = entries ?? derived?.entries;
  if (!base?.length) return null;
  // Uniform derived legend: one swatch, label it with the dataset name (the derived label is "").
  const uniform = !entries && derived?.uniform;
  const items = uniform ? [{ label: name ?? "All features", color: base[0].color }] : base;
  const heading = title ?? derived?.field ?? "Legend";
  // Grouped legend (entries carry `values`): stack each group's colour + label, with the
  // specific values it rolls up spelled out beneath. Otherwise the flat inline-wrap layout.
  const grouped = items.some((e) => e.values && e.values.length > 0);
  return (
    <div className="mt-2 rounded-md border border-border bg-card p-2.5 text-xs">
      <div className="mb-1.5 font-semibold text-muted-foreground">{heading}</div>
      {grouped ? (
        <div className="flex flex-col gap-1.5">
          {items.map((e, i) => (
            <div key={i} className="flex flex-col gap-0.5">
              <span className="inline-flex items-center gap-1.5 font-medium text-foreground">
                <span className="inline-block h-3 w-3 shrink-0 rounded-sm border border-border" style={{ background: e.color }} />
                {e.label}
              </span>
              {e.values && e.values.length > 0 && (
                <div className="flex flex-wrap gap-x-3 gap-y-1 pl-[1.375rem] text-muted-foreground">
                  {e.values.map((v) => (
                    <span key={v.value} className="inline-flex items-center gap-1">
                      <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm border border-border" style={{ background: v.color }} />
                      {v.label ?? v.value}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {items.map((e, i) => (
            <span key={i} className="inline-flex items-center gap-1.5 text-foreground">
              <span className="inline-block h-3 w-3 shrink-0 rounded-sm border border-border" style={{ background: e.color }} />
              {e.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
