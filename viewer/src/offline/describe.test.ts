import { describe as suite, expect, it } from "vitest";
import { describe, sortDescribed } from "./describe";

const BASE = "https://cdn.example/basemap/";
const LAYER = "https://cdn.example/warehouse/pmtiles/hazards_qfaults_current.pmtiles";
const SAVED = Date.parse("2026-09-01T00:00:00Z");

const item = (updated?: string) => ({
  href: "https://cdn.example/warehouse/stac/hazards/hazards_qfaults_current.json",
  data: {
    properties: { title: "Quaternary Faults", updated },
    assets: { pmtiles: { href: LAYER }, parquet: { href: "https://cdn.example/a.parquet" } },
  },
});

suite("describe", () => {
  it("names a layer by its catalog title and links back to the item", () => {
    const d = describe({ url: LAYER, bytes: 1, savedAt: SAVED }, [item()], BASE);
    expect(d).toMatchObject({ kind: "layer", label: "Quaternary Faults", itemHref: item().href, stale: false });
  });

  it("flags a layer the catalog has updated since it was saved", () => {
    expect(describe({ url: LAYER, bytes: 1, savedAt: SAVED }, [item("2026-09-20T00:00:00Z")], BASE).stale).toBe(true);
  });

  it("does not flag a layer saved after its last update", () => {
    expect(describe({ url: LAYER, bytes: 1, savedAt: SAVED }, [item("2026-08-20T00:00:00Z")], BASE).stale).toBe(false);
  });

  // A layer whose item is gone from the catalog (retired, or not loaded yet) still has to be
  // listable and deletable, or its bytes are stranded.
  it("falls back to the filename when no catalog item claims the file", () => {
    const d = describe({ url: LAYER, bytes: 1, savedAt: SAVED }, [], BASE);
    expect(d).toMatchObject({ kind: "layer", label: "hazards_qfaults_current.pmtiles", stale: false });
    expect(d.itemHref).toBeUndefined();
  });

  it("names the basemap overview and quads", () => {
    expect(describe({ url: `${BASE}overview.pmtiles`, bytes: 1, savedAt: SAVED }, [], BASE))
      .toMatchObject({ kind: "basemap", label: "Basemap overview (statewide)" });
    expect(describe({ url: `${BASE}quads/40111g8.pmtiles`, bytes: 1, savedAt: SAVED }, [], BASE))
      .toMatchObject({ kind: "basemap", label: "Basemap quad 40111g8" });
  });
});

suite("describe (links)", () => {
  // PMTiles is published as a rel="pmtiles" link rather than an asset; a stored archive must still
  // resolve to its item, or every downloaded vector layer shows as a bare filename.
  it("finds the item through a rel=pmtiles link", () => {
    const linked = {
      href: "https://cdn.example/item.json",
      data: { properties: { title: "CCUS Geologic Regions" }, assets: {}, links: [{ href: LAYER }] },
    };
    expect(describe({ url: LAYER, bytes: 1, savedAt: SAVED }, [linked], BASE).label).toBe("CCUS Geologic Regions");
  });
});

suite("sortDescribed", () => {
  it("lists layers, then the basemap overview, then quads by code", () => {
    const rows = [
      describe({ url: `${BASE}quads/40111g8.pmtiles`, bytes: 1, savedAt: SAVED }, [], BASE),
      describe({ url: LAYER, bytes: 1, savedAt: SAVED }, [item()], BASE),
      describe({ url: `${BASE}quads/40111b6.pmtiles`, bytes: 1, savedAt: SAVED }, [], BASE),
      describe({ url: `${BASE}overview.pmtiles`, bytes: 1, savedAt: SAVED }, [], BASE),
    ];
    expect(sortDescribed(rows).map((r) => r.label)).toEqual([
      "Quaternary Faults", "Basemap overview (statewide)", "Basemap quad 40111b6", "Basemap quad 40111g8",
    ]);
  });
});
