// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { toLayer } from "./app";
import { rootIndexItems } from "./stac";
import { layerRowsOf } from "./catalog/catalog";

// What `?l=`, the Map page's Add and the map search resolve a publication's layer and plate from:
// their root-index entries, tagged with their publication. The layer carries a `pmtiles` asset and a
// `rel=pmtiles` link naming its tile layer, the plate a COG asset, and neither a ugs:renders style.
const tagged = { "ugs:in_publication": "M-246DM", "ugs:publication_title": "Landslide maps of Utah" };
const [layer, plate] = rootIndexItems({ items: [
  { id: "M-246DM_UtahLandslideMaps__DebrisFlowPaths", bbox: [-113.05, 37.57, -109.2, 41.68],
    properties: { title: "Debris Flow Paths", ...tagged },
    assets: { data: { href: "https://x/f.parquet", type: "application/vnd.apache.parquet", roles: ["data"] },
              pmtiles: { href: "https://x/f.pmtiles", type: "application/vnd.pmtiles", roles: ["visual"] } },
    links: [{ rel: "self", href: "./ugs-publications/M/M-246DM/M-246DM_UtahLandslideMaps__DebrisFlowPaths/M-246DM_UtahLandslideMaps__DebrisFlowPaths.json" },
            { rel: "pmtiles", href: "https://x/f.pmtiles", type: "application/vnd.pmtiles", "pmtiles:layers": ["UtahLandslideMaps__DebrisFlowPaths"] }] },
  { id: "M-246DM_plate", bbox: [-113.05, 37.57, -109.2, 41.68], properties: { title: "Map plate", ...tagged },
    assets: { cog: { href: "https://x/p.cog.tif", type: "image/tiff; application=geotiff; profile=cloud-optimized", roles: ["data", "cloud-optimized"] } },
    links: [{ rel: "self", href: "./ugs-publications/M/M-246DM/M-246DM_plate/M-246DM_plate.json" }] },
] });

describe("toLayer on a publication's tagged root-index entries", () => {
  it("draws a publication layer from its PMTiles, unstyled, so the Map page's default style applies", () => {
    const l = toLayer(layer);
    expect(l).toMatchObject({ id: "M-246DM_UtahLandslideMaps__DebrisFlowPaths", pmHref: "https://x/f.pmtiles",
                              pmLayer: "UtahLandslideMaps__DebrisFlowPaths", tableHref: "https://x/f.parquet" });
    expect(l?.styleUrl).toBeUndefined();
  });

  it("draws the plate from its COG", () => {
    expect(toLayer(plate)).toMatchObject({ id: "M-246DM_plate", cogHref: "https://x/p.cog.tif" });
  });
});

describe("a publication's plate and layer in the Layers tray", () => {
  // The tray's rows are layerRowsOf over the root index, gated by the same toLayer check as app.tsx.
  it("gives an added plate id (and a layer id) a row, so ?l= never holds an id the tray cannot show", () => {
    const rows = layerRowsOf([layer, plate], {
      draws: (d) => toLayer({ collId: "", href: "layer/probe", data: d }) !== null,
      isLayer: () => false, collTitle: (id) => id,
    });
    expect(rows.map((r) => r.id).sort()).toEqual(["M-246DM_UtahLandslideMaps__DebrisFlowPaths", "M-246DM_plate"]);
    expect(rows.find((r) => r.id === "M-246DM_plate")).toMatchObject({
      title: "Map plate · Landslide maps of Utah", group: "ugs-publications/M" });
  });
});
