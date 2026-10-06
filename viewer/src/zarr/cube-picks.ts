/**
 * What a datacube shows — its variable and the step of each dim — in the URL, per item id:
 *   ?cube=DAYMET_DISALEXI~var=AET~time=2010-07,OTHER~month=7
 * One param for the item page and the map, so a shared link or "View on map" keeps the pick.
 */
import { useNavigate, useSearch } from "@tanstack/react-router";

/** `var` = the variable; any other key is a dim name → its step key (stac.stepKey). */
export type CubePicks = Record<string, string>;
export const VAR = "var";

export function parseCubeParam(raw: unknown): Record<string, CubePicks> {
  if (typeof raw !== "string" || !raw) return {};
  return Object.fromEntries(raw.split(",").flatMap((entry) => {
    const [id, ...pairs] = entry.split("~");
    if (!id) return [];
    return [[id, Object.fromEntries(pairs.flatMap((p) => {
      const at = p.indexOf("=");
      return at > 0 ? [[p.slice(0, at), p.slice(at + 1)]] : [];
    }))]];
  }));
}

export function cubeParam(all: Record<string, CubePicks>): string | undefined {
  const entries = Object.entries(all)
    .map(([id, picks]) => [id, ...Object.entries(picks).map(([k, v]) => `${k}=${v}`)])
    .filter((e) => e.length > 1);
  return entries.length ? entries.map((e) => e.join("~")).join(",") : undefined;
}

/** One cube's picks and a setter. Replaces history: stepping through months shouldn't fill Back. */
export function useCubePicks(id: string | undefined): [CubePicks, (key: string, value: string) => void] {
  const raw = useSearch({ from: "__root__", select: (s) => s.cube });
  const navigate = useNavigate();
  const picks = (id && parseCubeParam(raw)[id]) || {};
  const set = (key: string, value: string) => {
    if (!id) return;
    navigate({
      to: ".", replace: true,
      search: (prev) => {
        const all = parseCubeParam(prev.cube);
        return { ...prev, cube: cubeParam({ ...all, [id]: { ...all[id], [key]: value } }) };
      },
    });
  };
  return [picks, set];
}
