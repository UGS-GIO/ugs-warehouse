import { describe, expect, it } from "vitest";
import type { ItemRef } from "./browse";
import {
  applyFacets, bboxIntersects, extractFacets, filterByViewport, GEOM_HAS, GEOM_NONE,
  hasGeometry, sortItems, typeOf,
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
  // A fixture with distinct titles + datetimes; DS-9 is deliberately undated.
  const dated: ItemRef[] = [
    item("c", "b-item", { title: "Beta", datetime: "2021-05-01T00:00:00Z" }),
    item("c", "a-item", { title: "Alpha", datetime: "2023-01-01T00:00:00Z" }),
    item("c", "c-item", { title: "Gamma", datetime: "2019-09-01T00:00:00Z" }),
    item("c", "d-item", { title: "Delta" }), // undated
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
});
