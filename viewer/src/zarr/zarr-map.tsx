/**
 * Item-detail pane for a zarr datacube. Generic over any store: `cube:variables` names what is
 * drawable, `cube:dimensions` which axes are non-spatial, and the store's own GeoZarr `spatial:*` /
 * `proj:wkt2` attrs place it. Variable and steps are picked into the URL (cube-picks).
 */
import type { Device } from "@luma.gl/core";
import maplibregl from "@/map/maplibre-lib";
import { useEffect, useMemo, useState } from "react";
import { Map as MapGL, NavigationControl } from "react-map-gl/maplibre";

import { type Asset, cubeRenderRescale, cubeStepDims, cubeVariables, idOf, resolveSelection, type StacDoc } from "@/stac";
import { DIRECT, protomapsStyle } from "@/map/basemap-style";
import { ensurePmtilesProtocol } from "@/map/pmtiles-protocol";
import { DeckOverlay } from "./zarr-overlay";
import { useSearch } from "@tanstack/react-router";
import { pickedRescale, useCubePicks, VAR } from "./cube-picks";
import { useCubeOfferSink } from "./cube-offer";
import { CubeControls } from "./step-picker";
import { useCubeSteps, useCubeStretch, useZarrLayers } from "./use-zarr-layers";
import { to2d } from "@/lib/bbox";

ensurePmtilesProtocol();

const LIGHT_BASEMAP = protomapsStyle("white", DIRECT);
const UTAH: [number, number, number, number] = [-114.1, 36.9, -108.9, 42.1];

export function ZarrMap({ asset, item }: { asset: Asset; item: StacDoc }) {
  const [device, setDevice] = useState<Device | null>(null);

  const variables = useMemo(() => Object.keys(cubeVariables(item)), [item]);
  // Keyed like the map's layers (the URL's item id), so the pick follows "View on map".
  const itemId = useSearch({ from: "__root__", select: (s) => (s.i ? idOf(s.i) : undefined) }) ?? String(item.id ?? "");
  const [picks, pick] = useCubePicks(itemId);
  const variable = picks[VAR] && variables.includes(picks[VAR]) ? picks[VAR] : variables[0];
  const stepDims = useCubeSteps({ href: asset.href, variable: variable ?? "" }, useMemo(() => cubeStepDims(item), [item]));
  const selection = resolveSelection(stepDims, picks);
  const stacRescale = useMemo(() => (variable ? cubeRenderRescale(item, variable) : undefined), [item, variable]);
  const stretch = useCubeStretch({ href: asset.href, variable: variable ?? "", stacRescale });
  const rescale = pickedRescale(picks);
  // On the item page the controls sit in the side column: offer them there.
  const offerTo = useCubeOfferSink();
  useEffect(() => {
    if (!offerTo || !variable) return;
    offerTo({ variables, variable, stepDims, stretch });
    return () => offerTo(null);
  }, [offerTo, variables, variable, stepDims, stretch]);
  const specs = variable
    ? [{ id: String(item.id ?? "cube"), href: asset.href, variable, selection, rescale, stacRescale }]
    : [];
  const { layers, states } = useZarrLayers(specs, device);
  const state = states[0];

  if (!variable) {
    return <Note>This item declares no <code>cube:variables</code>, so there is nothing to draw.</Note>;
  }
  if (state?.error) {
    return <Note tone="error">Could not open the datacube: {state.error.message}</Note>;
  }

  const [w, s, e, n] = to2d(item.bbox) ?? UTAH;
  const others = variables.length - 1;
  return (
    <div className="mt-2">
      <div className="h-96 w-full overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          mapLib={maplibregl}
          initialViewState={{ bounds: [w, s, e, n], fitBoundsOptions: { padding: 20 } }}
          mapStyle={LIGHT_BASEMAP}
          style={{ width: "100%", height: "100%" }}
        >
          <NavigationControl position="top-right" showCompass={false} />
          <DeckOverlay layers={layers} onDeviceInitialized={setDevice} />
        </MapGL>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        {!offerTo && <CubeControls variables={variables} variable={variable} stepDims={stepDims}
          selection={selection} stretch={stretch} rescale={rescale} onPick={pick} />}
        <p className="text-xs text-muted-foreground">
          {state?.isLoading
            ? "Opening datacube…"
            : <><code>{variable}</code> · viridis</>}
          {others > 0 && <> · {others} other variable{others > 1 ? "s" : ""} in this cube</>}
        </p>
      </div>
    </div>
  );
}

function Note({ children, tone }: { children: React.ReactNode; tone?: "error" }) {
  return (
    <div className={`mt-2 rounded-md border border-border bg-muted p-3 text-xs ${tone === "error" ? "text-destructive" : "text-muted-foreground"}`}>
      {children}
    </div>
  );
}
