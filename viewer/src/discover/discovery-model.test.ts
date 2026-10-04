import { describe, expect, it } from "vitest";
import type { ItemRef } from "@/catalog/browse";
import {
  activeChips, activeGroups, applyFacets, bboxIntersects, CLEAR_ALL, DEFAULT_DISCOVERY, type DiscoveryState,
  discoveryPatch, effectiveSort, extractFacets, filterByViewport, filterResults, GEOM_HAS, GEOM_NONE, hasGeometry,
  byOverlap, drawable, nearestStep, overlapScore, placeArea, unionOf, parseDiscovery, reliefs, withSelected, SCALE_STEPS, scaleBins, sortItems, typeOf, yearBins, yearSpan,
} from "./discovery-model";

// Minimal item factory — only the fields the discovery core reads (collId, id, bbox, properties).
const item = (collId: string, id: string, props: Record<string, unknown> = {}, bbox?: number[]): ItemRef =>
  ({ collId, href: `https://x/${collId}/${id}/${id}.json`, data: { id, bbox, properties: props } });

// A tiny Utah-ish fixture: two collections, three types, mixed geometry (one aspatial pub).
const items: ItemRef[] = [
  item("ugs-serving-topics/hazards", "hazards_qfaults", { "ugs:topic": "hazards", title: "Quaternary faults" }, [-114, 37, -109, 42]),
  item("ugs-serving-topics/hazards", "hazards_lsi", { "ugs:topic": "hazards", title: "Landslide inventory" }, [-112, 40, -111, 41]),
  item("ugs-publications", "GQ-1", { "ugs:pub_type": "Geologic Map", "ugs:series": "GQ", title: "Geologic map of X" }, [-113, 38, -112, 39]),
  item("ugs-publications", "DS-9", { "ugs:pub_type": "Data Series", "ugs:series": "DS", title: "Well data" }), // no bbox
];

describe("extractFacets", () => {
  it("counts collections by unique key (count desc, then key asc on a tie)", () => {
    const f = extractFacets(items);
    expect(f.collections.map((c) => [c.key, c.n])).toEqual([
      ["ugs-publications", 2],
      ["ugs-serving-topics/hazards", 2],
    ]);
    // label is the leaf folder of the collection key
    expect(f.collections.find((c) => c.key === "ugs-serving-topics/hazards")?.label).toBe("hazards");
  });

  it("derives a coarse type facet (pub_type, else topic)", () => {
    const byKey = Object.fromEntries(extractFacets(items).types.map((t) => [t.key, t.n]));
    expect(byKey).toEqual({ hazards: 2, "Geologic Map": 1, "Data Series": 1 });
  });

  it("splits has-geometry from aspatial items", () => {
    expect(extractFacets(items).geometry).toEqual([
      { key: GEOM_HAS, label: "On the map", n: 3 },
      { key: GEOM_NONE, label: "No footprint", n: 1 },
    ]);
  });

  it("ignores items with no loaded data", () => {
    const f = extractFacets([...items, { collId: "ugs-publications", href: "https://x/pending.json" }]);
    expect(f.collections.find((c) => c.key === "ugs-publications")?.n).toBe(2);
  });
});

describe("hasGeometry / typeOf", () => {
  it("reads a valid bbox as geometry, rejects a missing one", () => {
    expect(hasGeometry(items[0])).toBe(true);
    expect(hasGeometry(items[3])).toBe(false);
  });
  it("prefers pub_type, falling back to topic then series", () => {
    expect(typeOf(items[2])).toBe("Geologic Map");
    expect(typeOf(items[0])).toBe("hazards");
  });
});

describe("applyFacets", () => {
  const ids = (out: ItemRef[]) => out.map((it) => it.data!.id);

  it("filters by collection (OR within the group)", () => {
    expect(ids(applyFacets(items, { collections: ["ugs-publications"], types: [], geometry: "all" })))
      .toEqual(["GQ-1", "DS-9"]);
  });
  it("filters by type", () => {
    expect(ids(applyFacets(items, { collections: [], types: ["hazards"], geometry: "all" })))
      .toEqual(["hazards_qfaults", "hazards_lsi"]);
  });
  it("filters to has-geometry and to aspatial", () => {
    expect(ids(applyFacets(items, { collections: [], types: [], geometry: GEOM_HAS })))
      .toEqual(["hazards_qfaults", "hazards_lsi", "GQ-1"]);
    expect(ids(applyFacets(items, { collections: [], types: [], geometry: GEOM_NONE })))
      .toEqual(["DS-9"]);
  });
  it("ANDs across groups", () => {
    expect(ids(applyFacets(items, { collections: ["ugs-serving-topics/hazards"], types: ["hazards"], geometry: GEOM_HAS })))
      .toEqual(["hazards_qfaults", "hazards_lsi"]);
  });
  it("no selection keeps every loaded item", () => {
    expect(ids(applyFacets(items, { collections: [], types: [], geometry: "all" }))).toHaveLength(4);
  });
});

describe("bboxIntersects / filterByViewport", () => {
  it("detects overlap, containment, and disjoint boxes", () => {
    expect(bboxIntersects([-114, 37, -109, 42], [-112, 40, -111, 41])).toBe(true); // contained
    expect(bboxIntersects([-114, 37, -113, 38], [-100, 40, -99, 41])).toBe(false); // disjoint
    expect(bboxIntersects(undefined, [-112, 40, -111, 41])).toBe(false); // aspatial
  });

  it("keeps only items whose footprint meets the viewport; drops aspatial ones", () => {
    // A viewport over the Wasatch Front: matches statewide qfaults + lsi, not the SW-corner GQ-1.
    const out = filterByViewport(items, [-111.9, 40.5, -111.8, 40.7]);
    expect(out.map((it) => it.data!.id)).toEqual(["hazards_qfaults", "hazards_lsi"]);
  });

  it("returns everything when the viewport is missing/invalid", () => {
    expect(filterByViewport(items, undefined)).toHaveLength(items.length);
  });
});

describe("sortItems", () => {
  // Publications with distinct titles + publication dates; Delta is deliberately undated.
  const dated: ItemRef[] = [
    item("c", "b-item", { title: "Beta", "ugs:series_id": "B-1", datetime: "2021-05-01T00:00:00Z" }),
    item("c", "a-item", { title: "Alpha", "ugs:series_id": "A-1", datetime: "2023-01-01T00:00:00Z" }),
    item("c", "c-item", { title: "Gamma", "ugs:series_id": "C-1", datetime: "2019-09-01T00:00:00Z" }),
    item("c", "d-item", { title: "Delta", "ugs:series_id": "D-1" }), // undated
  ];
  const titles = (out: ItemRef[]) => out.map((it) => String(it.data!.properties!.title));

  it("relevance preserves the caller's order (identity)", () => {
    const out = sortItems(dated, "relevance");
    expect(out).toBe(dated); // same reference — no copy, no reorder
  });

  it("title sorts A–Z and does not mutate the input", () => {
    const out = sortItems(dated, "title");
    expect(titles(out)).toEqual(["Alpha", "Beta", "Delta", "Gamma"]);
    expect(titles(dated)).toEqual(["Beta", "Alpha", "Gamma", "Delta"]); // input untouched
  });

  it("newest sorts by datetime desc, undated last", () => {
    expect(titles(sortItems(dated, "newest"))).toEqual(["Alpha", "Beta", "Gamma", "Delta"]);
  });

  it("oldest sorts by datetime asc, undated still last", () => {
    expect(titles(sortItems(dated, "oldest"))).toEqual(["Gamma", "Beta", "Alpha", "Delta"]);
  });

  it("does not count a layer's load date as new or old", () => {
    const layer = item("ugs-serving-topics/hazards", "hazards_qfaults",
      { title: "Quaternary faults", "ugs:dbt_schema": "hazards", datetime: "2026-09-30T18:00:00Z" });
    const mixed = [layer, ...dated.slice(0, 2)];
    expect(titles(sortItems(mixed, "newest"))).toEqual(["Alpha", "Beta", "Quaternary faults"]);
    expect(titles(sortItems(mixed, "oldest"))).toEqual(["Beta", "Alpha", "Quaternary faults"]);
  });

  it("still orders layers among themselves by when they were loaded", () => {
    const loaded = (title: string, datetime: string) =>
      item("ugs-serving-topics/hazards", title, { title, "ugs:dbt_schema": "hazards", datetime });
    const layers = [loaded("Older load", "2026-08-01T00:00:00Z"), loaded("Newer load", "2026-09-30T00:00:00Z")];
    expect(titles(sortItems(layers, "newest"))).toEqual(["Newer load", "Older load"]);
    expect(titles(sortItems(layers, "oldest"))).toEqual(["Older load", "Newer load"]);
  });
});

describe("category & format facets", () => {
  const withAssets: ItemRef[] = [
    item("ugs-serving-topics/emp", "wells", { "ugs:dbt_schema": "emp", title: "Wells" }, [-114, 37, -109, 42]),
    item("ugs-serving-topics/hazards", "qf", { "ugs:dbt_schema": "hazards", title: "Faults" }, [-112, 40, -111, 41]),
    { collId: "ugs-publications/GQ", href: "https://x/ugs-publications/GQ/GQ-1/GQ-1.json",
      data: { id: "GQ-1", properties: { "ugs:series": "GQ", title: "Map" }, assets: { pub: { href: "a.pdf", type: "application/pdf" } } } },
  ];

  it("counts one home category per item and buckets formats", () => {
    const f = extractFacets(withAssets);
    expect(Object.fromEntries(f.categories.map((c) => [c.key, c.n]))).toEqual({
      "energy-minerals": 1, hazards: 1, publications: 1,
    });
    expect(f.formats.map((x) => x.key)).toEqual(["PDF"]);
  });

  it("filters by category and by format (OR within, AND across)", () => {
    const ids = (out: ItemRef[]) => out.map((it) => it.data!.id);
    expect(ids(applyFacets(withAssets, { collections: [], types: [], categories: ["hazards"], geometry: "all" })))
      .toEqual(["qf"]);
    expect(ids(applyFacets(withAssets, { collections: [], types: [], formats: ["PDF"], geometry: "all" })))
      .toEqual(["GQ-1"]);
  });
});

describe("parseDiscovery / discoveryPatch (the URL boundary)", () => {
  it("defaults a bare/garbage search to the default state", () => {
    expect(parseDiscovery({})).toEqual(DEFAULT_DISCOVERY);
    expect(parseDiscovery({ sort: "bogus", layout: "x", geometry: "nope", area: "1,2" }))
      .toEqual(DEFAULT_DISCOVERY);
  });

  it("parses CSV lists, enums, and a valid area; drops an invalid area", () => {
    const s = parseDiscovery({
      q: "faults", collections: "a,b,a", category: "hazards", types: "Report",
      formats: "PDF,COG", geometry: "has", sort: "newest", layout: "list",
      density: "compact", area: "-114,37,-109,42",
    });
    expect(s).toEqual({
      q: "faults", collections: ["a", "b"], categories: ["hazards"], types: ["Report"],
      formats: ["PDF", "COG"], geometry: "has", sort: "newest", layout: "list",
      density: "compact", area: [-114, 37, -109, 42], place: "", years: null, scales: null,
    });
    expect(parseDiscovery({ area: "999,999,0,0" }).area).toBeNull();
  });

  it("serializes a state back, dropping defaults (a pristine view is a clean URL)", () => {
    expect(discoveryPatch(DEFAULT_DISCOVERY)).toEqual({
      q: undefined, collections: undefined, category: undefined, types: undefined,
      formats: undefined, geometry: undefined, sort: undefined, layout: undefined,
      density: undefined, area: undefined, place: undefined, years: undefined, scale: undefined,
    });
    const patched = discoveryPatch({ ...DEFAULT_DISCOVERY, q: "x", categories: ["hazards"], area: [-114, 37, -109, 42], sort: "newest" });
    expect(patched.q).toBe("x");
    expect(patched.category).toBe("hazards");
    expect(patched.area).toBe("-114,37,-109,42");
    expect(patched.sort).toBe("newest");
  });

  it("round-trips through parse → patch → parse", () => {
    const s = parseDiscovery({ q: "x", collections: "a,b", category: "hazards", geometry: "none", sort: "oldest" });
    expect(parseDiscovery(discoveryPatch(s) as Record<string, unknown>)).toEqual(s);
  });

  it("preserves spaces in q so a multi-word query survives the per-keystroke URL round-trip", () => {
    // The box is controlled from the URL; trimming on write would strip the space the instant it's
    // typed, collapsing "salt lake" → "saltlake". Internal AND trailing spaces must survive.
    expect(discoveryPatch({ ...DEFAULT_DISCOVERY, q: "salt lake" }).q).toBe("salt lake");
    expect(discoveryPatch({ ...DEFAULT_DISCOVERY, q: "salt lake " }).q).toBe("salt lake ");
    expect(parseDiscovery({ q: "salt lake" }).q).toBe("salt lake");
    // A whitespace-only query is still dropped (it's not a real search).
    expect(discoveryPatch({ ...DEFAULT_DISCOVERY, q: "   " }).q).toBeUndefined();
  });
});

describe("activeChips", () => {
  const labelFor = { collection: (k: string) => `Coll ${k}`, category: (k: string) => `Cat ${k}` };
  it("lists a removable chip per active filter, and each chip's patch removes only itself", () => {
    const s: DiscoveryState = { ...DEFAULT_DISCOVERY, collections: ["a", "b"], categories: ["hazards"], geometry: GEOM_HAS, area: [-1, 0, 1, 2] };
    const chips = activeChips(s, labelFor);
    expect(chips.map((c) => c.label)).toEqual(["Coll a", "Coll b", "Cat hazards", "On the map", "Map area"]);
    // removing collection "a" leaves "b"
    expect(chips[0].patch.collections).toEqual(["b"]);
    expect(chips.find((c) => c.id === "area")?.patch.area).toBeNull();
  });
  it("has no chips for a pristine state", () => {
    expect(activeChips(DEFAULT_DISCOVERY, labelFor)).toEqual([]);
  });
});

describe("effectiveSort", () => {
  const at = (q: string, sort: DiscoveryState["sort"] = "relevance") =>
    effectiveSort({ ...DEFAULT_DISCOVERY, q, sort });
  it("lists newest first when there are no words to rank by", () => {
    expect(at("")).toBe("newest");
    expect(at("series:GQ")).toBe("newest");
  });
  it("keeps best match for a word query", () => {
    expect(at("salt lake")).toBe("relevance");
  });
  it("keeps a sort the user picked", () => {
    expect(at("", "title")).toBe("title");
    expect(at("salt lake", "oldest")).toBe("oldest");
  });
});

// Publications carry `ugs:series_id`, which is what makes their datetime a publication date.
const pub = (id: string, props: Record<string, unknown>, bbox?: number[]) =>
  item("ugs-publications/M", id, { "ugs:series_id": id, "ugs:pub_type": "Map", title: id, ...props }, bbox);
const pubs: ItemRef[] = [
  pub("M-1", { datetime: "1955-01-01T00:00:00Z", "ugs:scale": "1:24,000" }, [-110, 38, -109.9, 38.1]),
  pub("M-2", { datetime: "1993-01-01T00:00:00Z", "ugs:scale": "1:100,000" }, [-110.5, 38, -109, 39]),
  pub("M-3", { datetime: "2002-01-01T00:00:00Z", "ugs:scale_denominator": 24000 }),
  pub("M-4", { "ugs:scale": "1:250,000" }),                       // undated
  item("ugs-serving-topics/hazards", "hazards_qfaults", { "ugs:topic": "hazards", datetime: "2026-01-01T00:00:00Z" }),
];

describe("year + scale ranges", () => {
  it("round-trip through the URL, open ends included", () => {
    const s: DiscoveryState = { ...DEFAULT_DISCOVERY, years: [1990, null], scales: [24000, 100000] };
    const patched = discoveryPatch(s);
    expect(patched.years).toBe("1990,");
    expect(patched.scale).toBe("24000,100000");
    expect(parseDiscovery(patched as Record<string, unknown>)).toEqual(s);
    expect(parseDiscovery({ years: "2010,1990" }).years).toEqual([1990, 2010]);
    expect(parseDiscovery({ years: "," }).years).toBeNull();
    expect(parseDiscovery({ years: "abc,1990" }).years).toBeNull();
    expect(parseDiscovery({ scale: "0," }).scales).toBeNull();
    expect(parseDiscovery({ scale: "-5,10" }).scales).toBeNull();
    expect(parseDiscovery({ years: "1990.5," }).years).toBeNull();
  });

  it("keep only items with a value in range; a layer has no publication year", () => {
    const ids = (s: Partial<DiscoveryState>) => filterResults(pubs, { ...DEFAULT_DISCOVERY, ...s }).map((it) => it.data?.id);
    expect(ids({ years: [1990, 2010] })).toEqual(["M-2", "M-3"]);
    expect(ids({ years: [null, 1960] })).toEqual(["M-1"]);
    expect(ids({ scales: [24000, 24000] })).toEqual(["M-1", "M-3"]);
    expect(ids({ scales: [50000, null] })).toEqual(["M-2", "M-4"]);
    // The filter steps like the slider: 1:20,000 sits at the 1:24,000 step, 1:63,360 at 1:62,500.
    const off = [pub("M-5", { "ugs:scale": "1:20,000" }), pub("M-6", { "ugs:scale": "1 inch = 1 mile" })];
    const pick = (r: [number, number]) => filterResults(off, { ...DEFAULT_DISCOVERY, scales: r }).map((it) => it.data?.id);
    expect(pick([24000, 24000])).toEqual(["M-5"]);
    expect(pick([62500, 62500])).toEqual(["M-6"]);
  });

  it("bin years and scales for the histograms", () => {
    expect(yearSpan(pubs)).toEqual([1955, 2002]);
    const bins = yearBins(pubs, [1950, 2004], 5);
    expect(bins[0]).toEqual({ lo: 1950, hi: 1954, n: 0 });
    expect(bins[1]).toEqual({ lo: 1955, hi: 1959, n: 1 });
    expect(bins.at(-1)).toEqual({ lo: 2000, hi: 2004, n: 1 });
    const sb = scaleBins(pubs);
    expect(sb[SCALE_STEPS.indexOf(24000)].n).toBe(2);
    expect(sb[SCALE_STEPS.indexOf(250000)].n).toBe(1);
    expect(nearestStep(30000)).toBe(SCALE_STEPS.indexOf(31680));
    expect(nearestStep(42240)).toBe(SCALE_STEPS.indexOf(50000));
  });

  it("chip labels name open ends plainly", () => {
    const labels = activeChips({ ...DEFAULT_DISCOVERY, years: [1990, null], scales: [null, 100000] },
      { collection: (k) => k, category: (k) => k }).map((c) => c.label);
    expect(labels).toEqual(["1990 or later", "1:100,000 or more detailed"]);
  });
});

describe("reliefs (what each filter hides)", () => {
  it("counts what dropping each active filter brings back, most first", () => {
    const s: DiscoveryState = { ...DEFAULT_DISCOVERY, years: [1990, 2010], scales: [24000, 24000] };
    const shown = filterResults(pubs, s).length;              // M-3
    expect(shown).toBe(1);
    expect(activeGroups(s)).toEqual(["years", "scales"]);
    expect(reliefs(pubs, s, shown).map((r) => [r.group, r.gain])).toEqual([["years", 1], ["scales", 1]]);
  });
  it("CLEAR_ALL drops every filter", () => {
    const s = { ...DEFAULT_DISCOVERY, categories: ["hazards"], area: [-1, 0, 1, 2] as DiscoveryState["area"],
      place: "Moab", years: [1990, null] as DiscoveryState["years"], q: "faults" };
    const cleared = { ...s, ...CLEAR_ALL };
    expect(activeGroups(cleared)).toEqual([]);
    expect(cleared.q).toBe("faults");
  });
});

describe("withSelected", () => {
  it("keeps a selected value the search left empty, at 0, first", () => {
    const f = [{ key: "hazards", label: "Hazards", n: 3 }];
    expect(withSelected(f, ["energy-minerals", "hazards"], (k) => k.toUpperCase())).toEqual([
      { key: "energy-minerals", label: "ENERGY-MINERALS", n: 0 }, { key: "hazards", label: "Hazards", n: 3 },
    ]);
    expect(withSelected(f, ["hazards"], String)).toBe(f);
  });
});

describe("searching an area", () => {
  const moab: [number, number, number, number] = [-109.58, 38.54, -109.52, 38.60];
  it("grows a town to about a 30' x 60' sheet around it", () => {
    const [w, s, e, n] = placeArea(moab);
    expect(e - w).toBeCloseTo(1);
    expect(n - s).toBeCloseTo(0.5);
    expect((w + e) / 2).toBeCloseTo(-109.55);
    expect(placeArea([-112, 37, -110, 39])).toEqual([-112, 37, -110, 39]);   // already big enough
  });
  it("ranks local maps first and statewide ones last", () => {
    const area = placeArea(moab);
    const quad = overlapScore([-109.627, 38.499, -109.498, 38.625], area);
    const sheet = overlapScore([-110.259, 38.479, -108.94, 39.011], area);
    const region = overlapScore([-111.5, 37.5, -108.9, 40.5], area);   // a layer over eastern Utah
    const state = overlapScore([-114.05, 37.0, -109.04, 42.0], area);
    expect(quad).toBeGreaterThan(sheet);     // all inside the area beats mostly inside
    expect(sheet).toBeGreaterThan(region);
    expect(region).toBeGreaterThan(state);
    expect(overlapScore([-111.9, 40.7, -111.8, 40.8], area)).toBe(0);
    expect(overlapScore([-109.55, 38.57, -109.55, 38.57], area)).toBeGreaterThan(0.5);
    const order = byOverlap([pub("S", {}, [-114.05, 37.0, -109.04, 42.0]), pub("Q", {}, [-109.627, 38.499, -109.498, 38.625])], area);
    expect(order.map((it) => it.data?.id)).toEqual(["Q", "S"]);
  });
  it("keeps 'Best match' as the default order while an area is set", () => {
    expect(effectiveSort({ ...DEFAULT_DISCOVERY, area: moab })).toBe("relevance");
    expect(effectiveSort(DEFAULT_DISCOVERY)).toBe("newest");
  });
});

describe("area edge cases and the map", () => {
  it("grows a point (an address) to the minimum area, centred on it", () => {
    expect(placeArea([-109.55, 38.57, -109.55, 38.57])).toEqual([-110.05, 38.32, -109.05, 38.82]);
  });
  it("rejects a zero-size or inverted area from the URL, and grows an old place link", () => {
    expect(parseDiscovery({ area: "-110,38,-110,39" }).area).toBeNull();
    expect(parseDiscovery({ area: "-109,38,-110,39" }).area).toBeNull();
    const s = parseDiscovery({ area: "-109.58,38.54,-109.52,38.60", place: "Moab" });
    expect(s.area![2] - s.area![0]).toBeCloseTo(1);
    expect(parseDiscovery(discoveryPatch(s) as Record<string, unknown>)).toEqual(s);
  });
  it("keeps statewide items in an area's results, but doesn't draw them", () => {
    const area: [number, number, number, number] = [-110.05, 38.32, -109.05, 38.82];
    const at = [pub("Q", {}, [-109.627, 38.499, -109.498, 38.625]), pub("S", {}, [-114.05, 37.0, -109.04, 42.0])];
    expect(filterResults(at, { ...DEFAULT_DISCOVERY, area, place: "Moab" }).map((it) => it.data?.id)).toEqual(["Q", "S"]);
    const fps = at.map((it) => ({ id: it.data!.id, bbox: it.data!.bbox! }));
    expect(drawable(fps, area).map((f) => f.id)).toEqual(["Q"]);
    expect(drawable(fps, null).map((f) => f.id)).toEqual(["Q"]);
    expect(unionOf(fps)).toEqual([-114.05, 37.0, -109.04, 42.0]);
    expect(unionOf([])).toBeNull();
  });
});
