// Legend derived from a bound ugs-styles GL fragment — no separate legend data. We read the
// paint color expression: `match` (categorical → value→color swatches) or `step` (graduated →
// range→color). Falls out of the style for free, so the legend can never drift from the render.

type Entry = { label: string; color: string };

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

export function legendFromStyle(
  layers: Array<Record<string, unknown>>,
): { field?: string; entries: Entry[] } | null {
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
  return null;
}

// `entries`/`title` override an explicit legend (icon renders carry no derivable paint — e.g.
// wells by-boxtype pie wedges). Otherwise derive from the style layers as before.
export function Legend({ layers, entries, title }: {
  layers?: Array<Record<string, unknown>>; entries?: Entry[]; title?: string;
}) {
  const derived = layers ? legendFromStyle(layers) : null;
  const items = entries ?? derived?.entries;
  if (!items?.length) return null;
  const heading = title ?? derived?.field ?? "Legend";
  return (
    <div className="mt-2 rounded-md border border-border bg-card p-2.5 text-xs">
      <div className="mb-1.5 font-semibold text-muted-foreground">{heading}</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1.5">
        {items.map((e, i) => (
          <span key={i} className="inline-flex items-center gap-1.5 text-foreground">
            <span className="inline-block h-3 w-3 shrink-0 rounded-sm border border-border" style={{ background: e.color }} />
            {e.label}
          </span>
        ))}
      </div>
    </div>
  );
}
