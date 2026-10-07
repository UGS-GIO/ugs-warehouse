// The item page's datacube controls hand-off (see cube-steps-context), split out for Fast Refresh.
import { createContext, useContext } from "react";

import type { CubeStep } from "@/stac";
import type { Stretch } from "./store";

export type CubeOffer = {
  variables: string[]; variable: string; stepDims: Record<string, CubeStep[]>; stretch?: Stretch;
};

export const Ctx = createContext<((offer: CubeOffer | null) => void) | null>(null);
export const OfferCtx = createContext<CubeOffer | null>(null);

/** Absent (the Discover drawer has no side column), the map keeps its controls under itself. */
export const useCubeOfferSink = () => useContext(Ctx);
