/**
 * Renders any STAC item whose asset is a zarr datacube — no per-dataset code. The item's
 * `cube:variables` name what can be drawn and `cube:dimensions` says which axis is time; the store
 * itself carries the CRS and transform (GeoZarr `spatial:*` / `proj:wkt2`), so placement needs no
 * configuration here. Variable and time step are pinned to the first of each for now (#139 phase 2
 * adds the pickers).
 *
 * Lazy-loaded: deck.gl-zarr pulls in luma.gl and the reprojection stack, which nothing else needs.
 */
import type { Device } from "@luma.gl/core";
import { createColormapTexture, decodeColormapSprite, COLORMAP_INDEX } from "@developmentseed/deck.gl-raster/gpu-modules";
import { ZarrLayer } from "@developmentseed/deck.gl-zarr";
import { MapboxOverlay, type MapboxOverlayProps } from "@deck.gl/mapbox";
import { useQuery } from "@tanstack/react-query";
import maplibregl from "maplibre-gl";
import { useMemo, useState } from "react";
import { Map as MapGL, NavigationControl, useControl } from "react-map-gl/maplibre";

// The library's own sprite, not a vendored copy — Vite hashes it and it tracks the package version.
import colormapsPng from "@developmentseed/deck.gl-raster/gpu-modules/colormaps.png?url";

import { type Asset, cubeVariables, type StacDoc, timeDimensionOf } from "./stac";
import { makeLocalEpsgResolver } from "./zarr/epsg";
import { openZarr, sampleRange } from "./zarr/store";
import { getTileData, makeRenderTile } from "./zarr/tile";

const POSITRON = "https://tiles.openfreemap.org/styles/positron";

/** deck.gl layers as a maplibre control. `interleaved` keeps basemap labels above the raster. */
function DeckOverlay(props: MapboxOverlayProps) {
  const overlay = useControl<MapboxOverlay>(() => new MapboxOverlay({ ...props, interleaved: true }));
  overlay.setProps(props);
  return null;
}

export function ZarrMap({ asset, item }: { asset: Asset; item: StacDoc }) {
  const [device, setDevice] = useState<Device | null>(null);

  const variables = useMemo(() => Object.keys(cubeVariables(item)), [item]);
  const variable = variables[0];
  const timeDim = timeDimensionOf(item);

  const { data: source, error, isLoading } = useQuery({
    queryKey: ["zarr-source", asset.href, variable],
    queryFn: async () => {
      const src = await openZarr(asset.href, variable);
      return { src, range: await sampleRange(src) };
    },
    enabled: !!variable,
    staleTime: 5 * 60_000,
    retry: false,
  });

  // One sprite holds every colormap's LUT; decode once, upload per device.
  const { data: sprite } = useQuery({
    queryKey: ["colormap-sprite"],
    queryFn: async () => decodeColormapSprite(await (await fetch(colormapsPng)).arrayBuffer()),
    staleTime: Infinity,
  });
  const colormapTexture = useMemo(
    () => (device && sprite ? createColormapTexture(device, sprite) : null),
    [device, sprite],
  );

  const layers = useMemo(() => {
    if (!source || !colormapTexture) return [];
    const { src, range } = source;
    return [
      new ZarrLayer({
        id: `zarr-${variable}`,
        node: src.array,
        // A cube with a leading time axis needs it pinned, or the slice stays 3D.
        selection: timeDim ? { [timeDim]: 0 } : {},
        opacity: 0.85,
        epsgResolver: makeLocalEpsgResolver(src.arrayAttrs),
        getTileData,
        renderTile: makeRenderTile({
          colormapTexture,
          colormapIndex: COLORMAP_INDEX.viridis,
          noDataValue: src.noDataValue,
          rescaleMin: range[0],
          rescaleMax: range[1],
        }),
      }),
    ];
  }, [source, colormapTexture, variable, timeDim]);

  if (!variable) {
    return <Note>This item declares no <code>cube:variables</code>, so there is nothing to draw.</Note>;
  }
  if (error) {
    return <Note tone="error">Could not open the datacube: {error instanceof Error ? error.message : String(error)}</Note>;
  }

  const [w, s, e, n] = (item.bbox ?? [-114.1, 36.9, -108.9, 42.1]).slice(0, 4);
  return (
    <div className="mt-2">
      <div className="h-96 w-full overflow-hidden rounded-md border border-border bg-muted">
        <MapGL
          mapLib={maplibregl}
          initialViewState={{ bounds: [w, s, e, n], fitBoundsOptions: { padding: 20 } }}
          mapStyle={POSITRON}
          style={{ width: "100%", height: "100%" }}
        >
          <NavigationControl position="top-right" showCompass={false} />
          <DeckOverlay layers={layers} onDeviceInitialized={setDevice} />
        </MapGL>
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {isLoading ? "Opening datacube…" : <><code>{variable}</code>{timeDim && <> · first {timeDim} step</>} · viridis, 2–98% of a sampled window</>}
        {variables.length > 1 && <> · {variables.length - 1} other variable{variables.length > 2 ? "s" : ""} in this cube</>}
      </p>
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
