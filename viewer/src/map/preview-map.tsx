// Cross-boundary state for the item-detail preview map: what to draw, where to portal it, and the
// wires the table shares with it. The maplibre half is `preview-map-gl.tsx`, loaded on first spec —
// this module carries no map code, so the catalog and doc views never pay for one.
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { type FocusSel, type MapPick, nextPick } from "./map-model";
import { type PreviewSpec, type Renders, specItemId } from "./preview-spec";
import { rendersOf } from "@/stac";
import { useDataSaver } from "@/lib/data-saver";
import { usePerItem } from "@/lib/use-per-item";

export { footprintSpecOf } from "./preview-spec";
export type { PreviewSpec } from "./preview-spec";

const PreviewMapGL = lazy(() => import("./preview-map-gl"));

type Ctx = {
  setSpec: (s: PreviewSpec) => void;
  registerSlot: (el: HTMLElement | null) => void;
  focus: FocusSel | null;
  setFocus: (f: FocusSel | null) => void;
  pick: MapPick | null;
  onFeatureClick: (id: number, props?: Record<string, unknown>) => void;
  featureRelated: { relatedKey: string; value: string } | null;
  openRelated: (relatedKey: string, value: string) => void;
  clearRelated: () => void;
  selectedFeature: { props: Record<string, unknown>; fid: number | null } | null;
  selectFeature: (props: Record<string, unknown>, fid: number | null) => void;
  clearSelection: () => void;
  // Set when the map finds a row's id on another record: the map and table are different versions.
  mapMismatch: boolean;
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

// Held as one value, exposed as the derived `selectedFeature` + `featureRelated` pair so consumers
// didn't change. Nesting `related` is what makes a new feature drop an open related table.
type Sel = {
  feature: { props: Record<string, unknown>; fid: number | null };
  related: { relatedKey: string; value: string } | null;
} | null;

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
  const [sel, setSel] = usePerItem<Sel>(itemId, null);
  const [mapMismatch, setMapMismatch] = usePerItem(itemId, false);
  const reportMismatch = useCallback(() => setMapMismatch(true), [setMapMismatch]);

  // Owned here, not mirrored up out of the map: the endpoints panel hands out the URL for the
  // symbology on screen, so both need the same copy.
  const renders: Renders = useMemo(() => (spec?.kind === "vector" ? rendersOf(spec.item) : {}), [spec]);
  const [chosen, setChosen] = usePerItem(itemId, "");
  const render = renders[chosen] ? chosen : renders.default ? "default" : Object.keys(renders)[0] ?? "";

  const setSpec = useCallback((s: PreviewSpec) => { setSpecState(s); if (s) setArmed(true); }, []);
  const registerSlot = useCallback((el: HTMLElement | null) => setSlotEl(el), []);
  const onFeatureClick = useCallback((id: number, props?: Record<string, unknown>) => setPick((p) => nextPick(p, id, props)), [setPick]);
  const selectFeature = useCallback((props: Record<string, unknown>, fid: number | null) => setSel({ feature: { props, fid }, related: null }), [setSel]);
  const clearSelection = useCallback(() => setSel(null), [setSel]);
  const openRelated = useCallback((relatedKey: string, value: string) => setSel((s) => (s ? { ...s, related: { relatedKey, value } } : s)), [setSel]);
  const clearRelated = useCallback(() => setSel((s) => (s ? { ...s, related: null } : s)), [setSel]);

  const ctx = useMemo<Ctx>(
    () => ({
      setSpec, registerSlot, focus, setFocus, pick, onFeatureClick,
      featureRelated: sel?.related ?? null, openRelated, clearRelated,
      selectedFeature: sel?.feature ?? null, selectFeature, clearSelection, render, mapMismatch,
    }),
    [
      setSpec, focus, setFocus, pick, registerSlot, onFeatureClick, sel, openRelated, clearRelated,
      selectFeature, clearSelection, render, mapMismatch,
    ],
  );

  return (
    <PreviewMapCtx.Provider value={ctx}>
      {children}
      {armed && (
        <Suspense fallback={null}>
          <PreviewMapGL spec={spec} slotEl={slotEl} focus={focus} onFeatureClick={onFeatureClick} onMismatch={reportMismatch}
            onFeatureSelect={selectFeature} onClearSelection={clearSelection} renders={renders} sel={render} onSel={setChosen} />
        </Suspense>
      )}
    </PreviewMapCtx.Provider>
  );
}

// Per-preview placeholder: renders the box the map is portaled into, and publishes the spec. This is
// the ONLY thing that mounts/unmounts per item — a cheap DOM node, no WebGL.
export function PreviewMapSlot({ spec }: { spec: PreviewSpec }) {
  const { setSpec, registerSlot } = usePreviewMap();
  // Data saver holds the map back (no map code, no tiles) until asked for. Local to the slot, which
  // mounts per item, so each item asks again.
  const saver = useDataSaver();
  const [asked, setAsked] = useState(false);
  const held = saver && !asked && spec !== null;

  // Publish spec on change. Kept in an effect so render stays pure.
  useEffect(() => { setSpec(held ? null : spec); }, [spec, setSpec, held]);
  // React 19 runs a ref callback's cleanup on unmount, so no mount/unmount effect is needed.
  const slotRef = useCallback((el: HTMLDivElement | null) => {
    registerSlot(el);
    return () => { registerSlot(null); setSpec(null); };
  }, [registerSlot, setSpec]);

  if (held) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border bg-muted/40 p-4 text-center">
        <button type="button" onClick={() => setAsked(true)}
          className="rounded-md bg-primary px-5 py-3 text-base font-semibold text-primary-foreground shadow hover:bg-primary/90">
          Show map
        </button>
        <p className="max-w-72 text-xs text-muted-foreground">
          Data saver is on, so the map is not loaded. Downloads below work without it.
        </p>
      </div>
    );
  }
  return <div ref={slotRef} />;
}
