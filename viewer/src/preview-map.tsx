// Cross-boundary state for the item-detail preview map: what to draw, where to portal it, and the
// wires the table shares with it. The maplibre half is `preview-map-gl.tsx`, loaded on first spec —
// this module carries no map code, so the catalog and doc views never pay for one.
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { type FocusSel, type MapPick, nextPick } from "./map-model";
import { type PreviewSpec, type Renders, specItemId } from "./preview-spec";
import { rendersOf } from "./stac";
import { usePerItem } from "./use-per-item";

export { footprintSpecOf } from "./preview-spec";
export type { PreviewSpec } from "./preview-spec";

const PreviewMapGL = lazy(() => import("./preview-map-gl"));

type Ctx = {
  setSpec: (s: PreviewSpec) => void;
  registerSlot: (el: HTMLElement | null) => void;
  focus: FocusSel | null;
  setFocus: (f: FocusSel | null) => void;
  pick: MapPick | null;
  onFeatureClick: (id: number) => void;
  // Which `ugs:renders` entry the "Symbolize by" picker is on, so the endpoints panel can hand out
  // the style/ArcGIS URL for the symbology you are looking at rather than the first one.
  render: string;
};
const PreviewMapCtx = createContext<Ctx | null>(null);

export function usePreviewMap(): Ctx {
  const c = useContext(PreviewMapCtx);
  if (!c) throw new Error("usePreviewMap must be used within <PreviewMapProvider>");
  return c;
}

export function PreviewMapProvider({ children }: { children: React.ReactNode }) {
  const [spec, setSpecState] = useState<PreviewSpec>(null);
  const [slotEl, setSlotEl] = useState<HTMLElement | null>(null);
  // Once a spec has arrived the map stays mounted even when it goes null — the point of it is that
  // its GL context is never torn down.
  const [armed, setArmed] = useState(false);
  const itemId = specItemId(spec);
  // Scoped to the shown item — a stale fly/highlight would mislead.
  const [focus, setFocus] = usePerItem<FocusSel | null>(itemId, null);
  const [pick, setPick] = usePerItem<MapPick | null>(itemId, null);

  // Owned here, not mirrored up out of the map: the endpoints panel hands out the URL for the
  // symbology on screen, so both need the same copy.
  const renders: Renders = useMemo(() => (spec?.kind === "vector" ? rendersOf(spec.item) : {}), [spec]);
  const [chosen, setChosen] = usePerItem(itemId, "");
  const render = renders[chosen] ? chosen : renders.default ? "default" : Object.keys(renders)[0] ?? "";

  const setSpec = useCallback((s: PreviewSpec) => { setSpecState(s); if (s) setArmed(true); }, []);
  const registerSlot = useCallback((el: HTMLElement | null) => setSlotEl(el), []);
  const onFeatureClick = useCallback((id: number) => setPick((p) => nextPick(p, id)), [setPick]);

  const ctx = useMemo<Ctx>(
    () => ({ setSpec, registerSlot, focus, setFocus, pick, onFeatureClick, render }),
    [setSpec, focus, setFocus, pick, registerSlot, onFeatureClick, render],
  );

  return (
    <PreviewMapCtx.Provider value={ctx}>
      {children}
      {armed && (
        <Suspense fallback={null}>
          <PreviewMapGL spec={spec} slotEl={slotEl} focus={focus} onFeatureClick={onFeatureClick}
            renders={renders} sel={render} onSel={setChosen} />
        </Suspense>
      )}
    </PreviewMapCtx.Provider>
  );
}

// Per-preview placeholder: renders the box the map is portaled into, and publishes the spec. This is
// the ONLY thing that mounts/unmounts per item — a cheap DOM node, no WebGL.
export function PreviewMapSlot({ spec }: { spec: PreviewSpec }) {
  const { setSpec, registerSlot } = usePreviewMap();
  const elRef = useRef<HTMLDivElement>(null);

  // Publish spec on change. Kept in an effect so render stays pure.
  useEffect(() => { setSpec(spec); }, [spec, setSpec]);
  // Register/clear this slot as the portal target across mount/unmount.
  useEffect(() => {
    registerSlot(elRef.current);
    return () => { registerSlot(null); setSpec(null); };
  }, [registerSlot, setSpec]);

  return <div ref={elRef} />;
}
