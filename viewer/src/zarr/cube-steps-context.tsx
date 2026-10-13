/**
 * Lets the item page show a datacube's controls beside the map instead of under it. The map
 * publishes what it can draw (steps cut to the opened store); the side panel picks into the URL.
 * Light on purpose: the side column must not pull the deck.gl-zarr chunk in.
 */
import { useSearch } from "@tanstack/react-router";
import { type ReactNode, useContext, useState } from "react";

import { Panel } from "@/catalog/panel";
import { idOf, resolveSelection } from "@/stac";
import { type CubeOffer, Ctx, OfferCtx } from "./cube-offer";
import { pickedRescale, useCubePicks } from "./cube-picks";
import { CubeControls } from "./step-picker";
import { hasCubeControls } from "./steps";


export function CubeStepsProvider({ children }: { children: ReactNode }) {
  const [offer, setOffer] = useState<CubeOffer | null>(null);
  return <Ctx.Provider value={setOffer}><OfferCtx.Provider value={offer}>{children}</OfferCtx.Provider></Ctx.Provider>;
}

export function CubeStepsPanel() {
  const offer = useContext(OfferCtx);
  // `i` may be a full item URL; picks are keyed by the short id, like the map's layers.
  const itemId = useSearch({ from: "__root__", select: (s) => (s.i ? idOf(s.i) : undefined) });
  const [picks, pick] = useCubePicks(itemId);
  if (!offer || !(offer.stretch || hasCubeControls(offer.variables, offer.stepDims))) return null;
  return (
    <Panel title="Datacube">
      <CubeControls {...offer} selection={resolveSelection(offer.stepDims, picks)}
        rescale={pickedRescale(picks)} onPick={pick} />
    </Panel>
  );
}
