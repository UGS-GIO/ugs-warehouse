// The picker logic, kept out of the component file so Fast Refresh can reload it.
import type { CubeStep } from "@/stac";

export const FIELDS = ["year", "month", "day"] as const;
export type Field = (typeof FIELDS)[number];

/**
 * The step a field change lands on: coarser fields stay put, finer ones stay as close as the series
 * allows (Mar 2010 → 2011 is Mar 2011, or the nearest month 2011 has).
 */
export function stepFor(steps: CubeStep[], cur: CubeStep, field: Field, value: number): number {
  const at = FIELDS.indexOf(field);
  const coarser = FIELDS.slice(0, at);
  const finer = FIELDS.slice(at + 1);
  let best = -1, bestDist = Infinity;
  steps.forEach((s, i) => {
    if (s[field] !== value || coarser.some((f) => s[f] !== cur[f])) return;
    // Month weighs more than day, so the nearest month wins before the nearest day.
    const dist = finer.reduce((d, f) => d * 40 + Math.abs((s[f] ?? 0) - (cur[f] ?? 0)), 0);
    if (dist < bestDist) { best = i; bestDist = dist; }
  });
  return best;
}

/** Whether a cube has anything to pick: a second variable or a dim with more than one step. */
export const hasCubeControls = (variables: string[], stepDims: Record<string, CubeStep[]>) =>
  variables.length > 1 || Object.values(stepDims).some((s) => s.length > 1);
