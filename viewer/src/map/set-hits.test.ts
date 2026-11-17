import { describe, expect, it } from "vitest";

import { type ClickedFeature, featureTarget, hitsOf } from "./set-hits";

const square: GeoJSON.Polygon = { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]] };
const line: GeoJSON.LineString = { type: "LineString", coordinates: [[2, 2], [3, 3]] };
const feat = (layerId: string, properties: Record<string, unknown>, geometry: GeoJSON.Geometry = square): ClickedFeature =>
  ({ layerId, source: `set-${layerId}`, sourceLayer: layerId, properties, geometry });

describe("featureTarget", () => {
  it("finds a feature by its id field, so its whole outline draws from its tiles", () => {
    expect(featureTarget(feat("polys", { OBJECTID: 311, Type: "joints" })).match)
      .toEqual({ source: "set-polys", sourceLayer: "polys", props: { OBJECTID: 311 } });
  });

  it("without an id field, matches all attributes when one is a measured length or area", () => {
    const props = { Symbol: "water/Qlmk", UnitSymbol: "water/Qlmk", Shape_Length: 40478.35219403969 };
    expect(featureTarget(feat("overlays", props)).match).toEqual({ source: "set-overlays", sourceLayer: "overlays", props });
  });

  it("with neither, draws the clicked geometry, so a line typed only 'fault' lights alone", () => {
    expect(featureTarget(feat("faults", { Type: "fault" }, line)).match).toEqual({ geometry: line });
  });
});

describe("hitsOf", () => {
  it("lists a polygon once though its fill and outline both answer the click, front first", () => {
    const poly = feat("polys", { OBJECTID: 1 });
    const hits = hitsOf([feat("points", { OBJECTID: 9 }, line), poly, { ...poly }], null, "idn-set", "units");
    expect(hits.map((h) => h.key)).toEqual(["points|OBJECTID=9", "polys|OBJECTID=1"]);
  });

  it("keeps two features apart when only their place differs", () => {
    const hits = hitsOf([feat("faults", { Type: "fault" }, line), feat("faults", { Type: "fault" }, square)], null, "idn-set", "units");
    expect(hits).toHaveLength(2);
  });

  it("adds the plate's unit last, lit by its stored code wherever it is mapped", () => {
    const unit = { kind: "unit" as const, unit: { seriesId: "OFR-784DM", code: "Qlmk", rank: 1 },
                   geometry: square, properties: { unit_code: "Qlmk", series_id: "OFR-784DM", rank: 1 } };
    const hits = hitsOf([feat("polys", { OBJECTID: 1 })], unit, "idn-set", "units");
    expect(hits.at(-1)).toMatchObject({ key: "unit", unit,
      match: { source: "idn-set", sourceLayer: "units", props: { unit_code: "Qlmk", series_id: "OFR-784DM" } } });
  });

  it("lists nothing for the scan-only answer", () => {
    expect(hitsOf([], { kind: "scan-only", pubTitle: "x" }, "idn-set", "units")).toEqual([]);
  });
});
