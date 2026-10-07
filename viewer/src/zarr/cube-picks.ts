/**
 * What a datacube shows — its variable and the step of each dim — in the URL, per item id:
 *   ?cube=DAYMET_DISALEXI~var=AET~time=2010-07,OTHER~month=7
 * One param for the item page and the map, so a shared link or "View on map" keeps the pick.
 */
import { useNavigate, useSearch } from "@tanstack/react-router";

/** `var` = the variable; `min`/`max` = the stretch; any other key is a dim name → its step key. */
export type CubePicks = Record<string, string>;
export const VAR = "var";
export const MIN = "min";
export const MAX = "max";

/** The user's Min/Max, each undefined when unset (the sampled stretch fills it). */
export function pickedRescale(picks: CubePicks): [number | undefined, number | undefined] {
  const num = (v?: string) => (v !== undefined && v !== "" && Number.isFinite(Number(v)) ? Number(v) : undefined);
  return [num(picks[MIN]), num(picks[MAX])];
}

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
export type SetPick = (key: string | Record<string, string | undefined>, value?: string) => void;

export function useCubePicks(id: string | undefined): [CubePicks, SetPick] {
  const raw = useSearch({ from: "__root__", select: (s) => s.cube });
  const navigate = useNavigate();
  const picks = (id && parseCubeParam(raw)[id]) || {};
  // `undefined` clears a key; a record sets several in one navigation. A new variable clears the
  // stretch: its range is the old variable's.
  const set: SetPick = (key, value) => {
    const changes = typeof key === "string" ? { [key]: value } : key;
    if (!id) return;
    navigate({
      to: ".", replace: true,
      search: (prev) => {
        const all = parseCubeParam(prev.cube);
        const next = { ...all[id], ...changes };
        if (VAR in changes) { delete next[MIN]; delete next[MAX]; }
        const clean = Object.fromEntries(Object.entries(next).filter(([, v]) => v !== undefined)) as CubePicks;
        return { ...prev, cube: cubeParam({ ...all, [id]: clean }) };
      },
    });
  };
  return [picks, set];
}
