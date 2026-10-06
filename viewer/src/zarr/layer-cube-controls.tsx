import type { ActiveLayer } from "@/map/map-model";
import { resolveSelection } from "@/stac";
import { pickedRescale, useCubePicks } from "./cube-picks";
import { CubeControls } from "./step-picker";
import { useCubeSteps, useCubeStretch } from "./use-zarr-layers";

/** A map layer row's controls: STAC's steps cut to the opened store, picks written to the URL. */
export function LayerCubeControls({ id, zarr }: { id: string; zarr: NonNullable<ActiveLayer["zarr"]> }) {
  const [picks, pick] = useCubePicks(id);
  const steps = useCubeSteps(zarr, zarr.stepDims);
  return <CubeControls variables={zarr.variables} variable={zarr.variable} stepDims={steps}
    selection={resolveSelection(steps, picks)} stretch={useCubeStretch(zarr)} rescale={pickedRescale(picks)}
    onPick={pick} />;
}
