import { describe, expect, it } from "vitest";
import { gateDir, gateOf, gateZoom, groupGate, MAX_ZOOM } from "./zoomgate";

// The real published fragment (maps-assets .../styles/styles/enmin_plss_sections/default.json after
// UGS-GIO/ugs-styles#35): a scale-gated outline plus its labels, both at the SLD's 250k denominator.
const PLSS_SECTIONS = [
  { id: "enmin_plss_sections-0", minzoom: 11.126916814491269, type: "line",
    paint: { "line-color": "#000000", "line-width": 1 } },
  { id: "enmin_plss_sections-1", minzoom: 11.126916814491269, type: "symbol",
    paint: { "text-opacity": 1, "text-color": "#000000" },
    layout: { "text-field": "{frstdivlab}", "text-size": 8 } },
];

describe("gateOf", () => {
  it("reads the gate off the drawing layer", () => {
    expect(gateOf(PLSS_SECTIONS)).toEqual({ min: 11.126916814491269, max: MAX_ZOOM });
  });

  it("returns null when nothing draws — labels alone are not a style", () => {
    // What enmin_plss_sections published BEFORE ugs-styles#35: the symbol layer only.
    expect(gateOf([PLSS_SECTIONS[1]])).toBeNull();
    expect(gateOf([])).toBeNull();
    expect(gateOf(undefined)).toBeNull();
    expect(gateOf(null)).toBeNull();
  });

  it("ignores a text-only symbol layer's own gate", () => {
    // The label is gated to z10 but the geometry draws from z0 — the layer is NOT hidden.
    const layers = [
      { type: "fill", paint: { "fill-color": "#eee" } },
      { type: "symbol", minzoom: 10, layout: { "text-field": "{name}" } },
    ];
    expect(gateOf(layers)).toEqual({ min: 0, max: MAX_ZOOM });
  });

  it("counts an icon-bearing symbol layer as drawing (the sprite renders)", () => {
    // enmin_ucrc_wells_current/by-boxtype is symbol-only and legitimately draws.
    const layers = [{ type: "symbol", minzoom: 4, layout: { "icon-image": "boxtype-core", "icon-size": 1 } }];
    expect(gateOf(layers)).toEqual({ min: 4, max: MAX_ZOOM });
  });

  it("takes the union across drawing layers", () => {
    const layers = [
      { type: "fill", minzoom: 8, maxzoom: 14, paint: { "fill-color": "#eee" } },
      { type: "line", minzoom: 5, paint: { "line-color": "#000" } },
    ];
    expect(gateOf(layers)).toEqual({ min: 5, max: MAX_ZOOM });
  });

  it("honours an explicit maxzoom", () => {
    expect(gateOf([{ type: "line", minzoom: 6, maxzoom: 12 }])).toEqual({ min: 6, max: 12 });
  });

  it("treats a non-numeric or non-finite zoom as no gate", () => {
    expect(gateOf([{ type: "line", minzoom: "11" }])).toEqual({ min: 0, max: MAX_ZOOM });
    expect(gateOf([{ type: "line", minzoom: NaN }])).toEqual({ min: 0, max: MAX_ZOOM });
  });
});

describe("gateDir", () => {
  const gate = { min: 11.126916814491269, max: MAX_ZOOM };

  it("says zoom in below the gate — the reported statewide fit lands near z6", () => {
    expect(gateDir(6, gate)).toBe("in");
    expect(gateDir(11.12, gate)).toBe("in");
  });

  it("is satisfied AT the gate — minzoom is inclusive", () => {
    expect(gateDir(11.126916814491269, gate)).toBeNull();
    expect(gateDir(14, gate)).toBeNull();
  });

  it("says zoom out above a maxzoom", () => {
    expect(gateDir(15, { min: 6, max: 12 })).toBe("out");
    expect(gateDir(12, { min: 6, max: 12 })).toBeNull();
  });

  it("has nothing to say when nothing draws", () => {
    expect(gateDir(6, null)).toBeNull();
  });
});

describe("groupGate", () => {
  const sections = { title: "PLSS Sections", gate: { min: 11.126916814491269, max: MAX_ZOOM } };
  const townships = { title: "PLSS Townships and Ranges", gate: { min: 10.126916814491269, max: MAX_ZOOM } };

  it("has nothing to show when no layer is gated out", () => {
    expect(groupGate([], "in")).toBeNull();
  });

  it("names every hidden layer", () => {
    expect(groupGate([sections, townships], "in")?.subject)
      .toBe("PLSS Sections, PLSS Townships and Ranges");
  });

  it("targets the DEEPEST gate so one zoom reveals all of them", () => {
    const g = groupGate([townships, sections], "in");
    expect(g?.gate.min).toBe(sections.gate.min);   // 11.13, not townships' 10.13
    // The move it offers satisfies BOTH layers, which is the point of taking the max.
    const z = gateZoom(g!.gate, "in");
    expect(gateDir(z, sections.gate)).toBeNull();
    expect(gateDir(z, townships.gate)).toBeNull();
  });

  it("takes the shallowest maxzoom in the zoom-out direction", () => {
    const g = groupGate([{ title: "A", gate: { min: 0, max: 12 } },
                         { title: "B", gate: { min: 0, max: 9 } }], "out");
    expect(g?.gate).toEqual({ min: 0, max: 9 });
    expect(gateDir(gateZoom(g!.gate, "out"), { min: 0, max: 12 })).toBeNull();
  });

  it("survives a layer whose style never resolved (no gate)", () => {
    expect(groupGate([{ title: "Loading", gate: null }], "in")?.gate).toEqual({ min: 0, max: MAX_ZOOM });
  });
});

describe("gateZoom", () => {
  const gate = { min: 11.126916814491269, max: 18 };

  it("targets the gate's own bound, not a rounded-down one that still draws nothing", () => {
    expect(gateZoom(gate, "in")).toBe(11.126916814491269);
    expect(gateDir(gateZoom(gate, "in"), gate)).toBeNull();   // the move actually satisfies the gate
    expect(gateZoom(gate, "out")).toBe(18);
    expect(gateDir(gateZoom(gate, "out"), gate)).toBeNull();
  });
});
