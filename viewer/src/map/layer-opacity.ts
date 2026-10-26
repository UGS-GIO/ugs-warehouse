// Each layer's opacity in the URL, `?op=geolunits~0.6,GQ-378~0.4`, so a shared link keeps it. Full
// opacity is the default and is left out.
import { useNavigate, useSearch } from "@tanstack/react-router";

export function parseOpacityParam(raw: unknown): Record<string, number> {
  if (typeof raw !== "string" || !raw) return {};
  return Object.fromEntries(raw.split(",").flatMap((entry) => {
    const [id, v] = entry.split("~");
    const o = Number(v);
    return id && v !== undefined && v !== "" && Number.isFinite(o) ? [[id, Math.min(Math.max(o, 0), 1)]] : [];
  }));
}

export function opacityParam(all: Record<string, number>): string | undefined {
  const entries = Object.entries(all).map(([id, o]) => [id, Math.round(o * 100) / 100] as const).filter(([, o]) => o < 1);
  return entries.length ? entries.map(([id, o]) => `${id}~${o}`).join(",") : undefined;
}

/** One layer's opacity and a setter. Replaces history: dragging a slider shouldn't fill Back. */
export function useLayerOpacity(id: string): [number, (o: number) => void] {
  const raw = useSearch({ from: "__root__", select: (s) => s.op });
  const navigate = useNavigate();
  const set = (o: number) => navigate({
    to: ".", replace: true,
    search: (prev) => ({ ...prev, op: opacityParam({ ...parseOpacityParam(prev.op), [id]: o }) }),
  });
  return [parseOpacityParam(raw)[id] ?? 1, set];
}

const OPACITY_PROPS = ["fill-opacity", "line-opacity", "circle-opacity", "circle-stroke-opacity",
  "fill-extrusion-opacity", "raster-opacity", "icon-opacity", "text-opacity"];
const PROP_OF: Record<string, string[]> = {
  fill: ["fill-opacity"], line: ["line-opacity"], circle: ["circle-opacity", "circle-stroke-opacity"],
  "fill-extrusion": ["fill-extrusion-opacity"], raster: ["raster-opacity"], symbol: ["icon-opacity", "text-opacity"],
};

// A zoom curve must stay the top of its expression, so its outputs are scaled instead of wrapping it.
function scale(v: unknown, o: number): unknown {
  if (typeof v === "number") return v * o;
  if (!Array.isArray(v)) return v;
  const op = v[0];
  const input = op === "interpolate" ? v[2] : v[1];
  const zoomCurve = (op === "interpolate" || op === "step") && Array.isArray(input) && input[0] === "zoom";
  if (!zoomCurve) return ["*", o, v];
  // interpolate: [op, how, input, stop, out, …]; step: [op, input, out0, stop, out, …]
  const firstOut = op === "interpolate" ? 4 : 2;
  return v.map((x, i) => (i >= firstOut && (i - firstOut) % 2 === 0 ? scale(x, o) : x));
}

/** A paint with every opacity of its layer `type` multiplied by `o`; unchanged at full opacity. */
export function withOpacity(paint: unknown, type: unknown, o: number): Record<string, unknown> | undefined {
  const base = paint && typeof paint === "object" ? { ...paint } : undefined;
  if (o >= 1) return base;
  const out: Record<string, unknown> = { ...base };
  for (const k of PROP_OF[String(type)] ?? OPACITY_PROPS.filter((p) => p in out)) out[k] = scale(out[k] ?? 1, o);
  return out;
}
