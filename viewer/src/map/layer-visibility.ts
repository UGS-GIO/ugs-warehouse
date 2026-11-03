// Layers on the map but switched off, `?hide=geolunits,GQ-378`. Unchecking a layer hides it and
// keeps its row, opacity and draw order; removing it is the row's × button.
import { useNavigate } from "@tanstack/react-router";

export const parseHideParam = (raw: unknown): Set<string> =>
  new Set(typeof raw === "string" && raw ? raw.split(",") : []);

/** The param for `hidden`, kept to ids still in `onMap` so a removed layer comes back visible. */
export function hideParam(hidden: Iterable<string>, onMap?: readonly string[]): string | undefined {
  const ids = [...hidden].filter((id) => !onMap || onMap.includes(id));
  return ids.length ? ids.join(",") : undefined;
}

/** Show or hide one layer. Replaces history, like the opacity slider. */
export function useSetHidden(): (id: string, hidden: boolean) => void {
  const navigate = useNavigate();
  return (id, hidden) => navigate({
    to: ".", replace: true,
    search: (prev) => {
      const ids = parseHideParam(prev.hide);
      if (hidden) ids.add(id); else ids.delete(id);
      return { ...prev, hide: hideParam(ids) };
    },
  });
}
