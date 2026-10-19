import type { StacDoc } from "@/stac";

// ---- datacube extension (zarr) ----
// https://github.com/stac-extensions/datacube — what turns a bare store href into something
// renderable: which dims are spatial vs. temporal, and which variables can be drawn.
type CubeDimension = { type?: string; axis?: string; extent?: unknown[]; values?: unknown[]; step?: unknown };
export type CubeVariable = { dimensions?: string[]; type?: string; unit?: string; description?: string };

const cubeProp = <T,>(item: StacDoc, key: string): Record<string, T> =>
  ((item.properties?.[key] ?? (item as Record<string, unknown>)[key]) as Record<string, T>) ?? {};

const cubeDimensions = (item: StacDoc): Record<string, CubeDimension> =>
  cubeProp<CubeDimension>(item, "cube:dimensions");

// Only variables STAC calls `data` are drawable; auxiliary/coordinate entries are metadata.
export const cubeVariables = (item: StacDoc): Record<string, CubeVariable> => {
  const all = cubeProp<CubeVariable>(item, "cube:variables");
  const data = Object.entries(all).filter(([, v]) => v.type === undefined || v.type === "data");
  return Object.fromEntries(data.length ? data : Object.entries(all));
};

// The dim a time picker drives. `type` is authoritative; the name is the fallback.
export const timeDimensionOf = (item: StacDoc): string | undefined =>
  Object.entries(cubeDimensions(item)).find(([n, d]) => d.type === "temporal" || n === "time")?.[0];

// Every dim that isn't x/y. ZarrLayer requires ALL of them pinned or sliced, and they are not all
// temporal — the climatology cubes key on `month` (type "other"), so keying off time alone throws.
export const nonSpatialDimensions = (item: StacDoc): string[] =>
  Object.entries(cubeDimensions(item))
    .filter(([n, d]) => d.type !== "spatial" && !["x", "y", "lat", "lon", "latitude", "longitude"].includes(n))
    .map(([n]) => n);

// One position along a non-spatial dim. The calendar fields a dated series resolves to drive the
// picker's selects (yearly: year; monthly: + month; daily: + day); anything else is just a label.
export type CubeStep = { label: string; year?: number; month?: number; day?: number };

export const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

type Resolution = "Y" | "M" | "D";

const monthStep = (month: number): CubeStep => ({ label: MONTH_NAMES[month - 1], month });

function dateStep(d: Date, res: Resolution): CubeStep {
  const year = d.getUTCFullYear(), month = d.getUTCMonth() + 1, day = d.getUTCDate();
  if (res === "Y") return { label: String(year), year };
  if (res === "M") return { label: `${MONTH_NAMES[month - 1]} ${year}`, year, month };
  return { label: `${MONTH_NAMES[month - 1]} ${day}, ${year}`, year, month, day };
}

const parseDate = (v: unknown): Date | undefined => {
  const d = typeof v === "string" ? new Date(v) : undefined;
  return d && !Number.isNaN(d.getTime()) ? d : undefined;
};

// Listed dates carry no step: the finest field that ever varies is the resolution.
const inferResolution = (ds: Date[]): Resolution =>
  ds.some((d) => d.getUTCDate() !== 1) ? "D" : ds.some((d) => d.getUTCMonth() !== 0) ? "M" : "Y";

// Generating more than this from an extent means a step we misread, not a real axis.
const MAX_STEPS = 5000;

/**
 * The steps of a non-spatial dim, in array order: from `values` when listed, else from `extent` +
 * `step` (yearly/monthly/daily ISO durations, or integers). Empty when STAC can't enumerate it.
 */
export function cubeSteps(item: StacDoc, dim: string): CubeStep[] {
  const d = cubeDimensions(item)[dim];
  if (!d) return [];
  const isMonth = dim === "month" || (d as { unit?: string }).unit === "month";
  const asMonth = (v: unknown) => (isMonth && Number.isInteger(v) && (v as number) >= 1 && (v as number) <= 12
    ? monthStep(v as number) : undefined);

  if (Array.isArray(d.values) && d.values.length) {
    const dates = d.values.map(parseDate);
    if (dates.every(Boolean)) {
      const res = inferResolution(dates as Date[]);
      return (dates as Date[]).map((t) => dateStep(t, res));
    }
    return d.values.map((v) => asMonth(v) ?? { label: String(v) });
  }
  const [lo, hi] = d.extent ?? [];
  const start = parseDate(lo), end = parseDate(hi);
  if (start && end) {
    const m = typeof d.step === "string" ? /^P(\d+)([YMD])$/.exec(d.step) : null;
    if (!m) return [];
    const n = Number(m[1]), res = m[2] as Resolution;
    if (n <= 0) return [];   // P0D would never advance
    const out: CubeStep[] = [];
    for (let i = 0; out.length < MAX_STEPS; i += n) {
      const t = new Date(Date.UTC(
        start.getUTCFullYear() + (res === "Y" ? i : 0),
        start.getUTCMonth() + (res === "M" ? i : 0),
        start.getUTCDate() + (res === "D" ? i : 0),
      ));
      if (Number.isNaN(t.getTime()) || t > end) break;   // past Date's range reads as NaN
      out.push(dateStep(t, res));
    }
    return out;
  }
  if (typeof lo === "number" && typeof hi === "number") {
    const step = typeof d.step === "number" && d.step > 0 ? d.step : 1;
    if ((hi - lo) / step >= MAX_STEPS) return [];
    const out: CubeStep[] = [];
    for (let v = lo; v <= hi; v += step) out.push(asMonth(v) ?? { label: String(v) });
    return out;
  }
  return [];
}

// Every pinned dim with its steps — what the step pickers draw and the layer slices by.
export const cubeStepDims = (item: StacDoc): Record<string, CubeStep[]> =>
  Object.fromEntries(nonSpatialDimensions(item).map((n) => [n, cubeSteps(item, n)]));

// A step as it reads in a URL: the date for a dated series (2010, 2010-07, 2010-07-15), the month
// number for a month dim, else the index. Survives the series growing at either end.
export function stepKey(step: CubeStep, index: number): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  if (step.year !== undefined) {
    return [String(step.year), step.month && pad(step.month), step.day && pad(step.day)].filter(Boolean).join("-");
  }
  return step.month !== undefined ? String(step.month) : String(index);
}

/** The picked step per dim, by key, as indexes. Unpicked or unknown: the latest step of a dated
 *  series (what people come for), else the first. */
export function resolveSelection(
  stepDims: Record<string, CubeStep[]>,
  picked: Record<string, string> = {},
): Record<string, number> {
  return Object.fromEntries(Object.entries(stepDims).map(([d, steps]) => {
    const at = picked[d] === undefined ? -1 : steps.findIndex((st, i) => stepKey(st, i) === picked[d]);
    const last = Math.max(0, steps.length - 1);
    return [d, at >= 0 ? at : steps[last]?.year !== undefined ? last : 0];
  }));
}

// A render-extension block (stac-extensions/render), titiler-style; `variable` picks a datacube
// variable the way titiler-xarray does.
type StacRender = { assets?: string[]; rescale?: number[][] | number[]; variable?: string };

/**
 * A datacube variable's display stretch from one STAC doc (an item, or its collection): the standard
 * render extension (`renders`), then the warehouse's `ugs:renders` (same shape). The render naming
 * this variable wins, else `default`. Undefined when the doc says nothing.
 */
export function cubeRenderRescale(item: StacDoc, variable: string): [number, number] | undefined {
  const props = (item.properties ?? {}) as Record<string, unknown>;
  for (const key of ["renders", "ugs:renders"]) {
    const renders = (props[key] ?? (item as Record<string, unknown>)[key]) as Record<string, StacRender> | undefined;
    if (!renders) continue;
    const r = Object.values(renders).find((x) => x.variable === variable) ?? renders.default;
    // The render extension nests one [min, max] per band; ugs-styles writes the single band flat.
    const rs = r?.rescale;
    const [lo, hi] = (Array.isArray(rs?.[0]) ? rs[0] : rs ?? []) as number[];
    if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo) return [lo, hi];
  }
  return undefined;
}
