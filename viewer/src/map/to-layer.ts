import type { ItemRef } from "@/catalog/browse";
import { unitsHrefOf } from "@/map/identify";
import type { ActiveLayer } from "@/map/map-model";
import { cogRenderAsset, collectionHrefOf, defaultStyleUrl, idOf, parquetAsset, pmtilesLink, pubOfEntry, rasterTilesAsset, type StacDoc, zarrAsset } from "@/stac";
import { cubeRenderRescale, cubeStepDims, cubeVariables, resolveSelection } from "@/stac/datacube";
import { type CubePicks, pickedRescale, VAR } from "@/zarr/cube-picks";

// An ItemRef → map ActiveLayer, by asset precedence: vector PMTiles, COG, raster mosaic, datacube.
// null when the item carries none of them — it isn't a layer.
export function toLayer(ref: ItemRef | undefined, cube: CubePicks = {}): ActiveLayer | null {
  if (!ref?.data) return null;
  const id = idOf(ref.href);
  const title = String(ref.data.properties?.title ?? id);
  const pm = pmtilesLink(ref.data);
  if (pm) {
    return {
      id, title,
      pmHref: pm.href,
      tableHref: parquetAsset(ref.data)?.href,
      pmLayer: pm["pmtiles:layers"]?.[0] ?? id,
      bbox: ref.data.bbox,
      styleUrl: defaultStyleUrl(ref.data),
    };
  }
  const cog = cogRenderAsset(ref.data);
  const identify = { unitsHref: unitsHrefOf(ref.data), pubTitle: pubOfEntry(ref.data)?.title };
  if (cog) return { id, title, cogHref: cog.href, bbox: ref.data.bbox, ...identify };
  const raster = rasterTilesAsset(ref.data);
  if (raster) return { id, title, rasterPmHref: raster.href, bbox: ref.data.bbox, ...identify };
  const zarr = zarrAsset(ref.data);
  // No drawable variable → not a layer, rather than a row that can never render.
  const variables = Object.keys(cubeVariables(ref.data));
  if (zarr && variables.length) {
    const stepDims = cubeStepDims(ref.data);
    const variable = cube[VAR] && variables.includes(cube[VAR]) ? cube[VAR] : variables[0];
    return {
      id, title, bbox: ref.data.bbox,
      zarr: {
        href: zarr.href, variable, variables, stepDims,
        selection: resolveSelection(stepDims, cube), rescale: pickedRescale(cube),
        stacRescale: cubeRenderRescale(ref.data, variable),
        collectionHref: collectionHrefOf(ref.data, ref.href),
      },
    };
  }
  return null;
}

// "Does this doc draw as a map layer?" — reuses toLayer's exact gate (the probe href/collId don't
// affect the answer), so the layer list and the Add-to-map button agree with what the map can
// actually render (e.g. a native-CRS COG that toLayer rejects isn't offered as a layer) and can't
// drift from toLayer.
export const drawsAsLayer = (data: StacDoc | undefined): boolean =>
  !!data && toLayer({ collId: "", href: "layer/probe", data }) !== null;
