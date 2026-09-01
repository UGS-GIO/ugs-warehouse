import { describe, expect, it } from "vitest";
import type { ItemRef } from "./browse";
import {
  author, bylineParts, categorize, categoryLabel, collectionLabel, collectionRoot, curatedDerived,
  dateOf, fmtDate, formatsOf, hasGeometry, itemKind, kindLabel, recordCountLabel, rowCount, scale,
  series, title, typeOf, year,
} from "./item-view";

// Minimal item factory — the fields the view-model reads (collId, id, bbox, properties, assets).
const item = (
  collId: string, id: string, props: Record<string, unknown> = {},
  extra: { bbox?: number[]; assets?: Record<string, unknown>; links?: unknown[] } = {},
): ItemRef => ({
  collId, href: `https://x/${collId}/${id}/${id}.json`,
  data: { id, bbox: extra.bbox, properties: props, assets: extra.assets as never, links: extra.links as never },
});

describe("field getters", () => {
  it("title falls back to the id, series is the id, collectionLabel is the leaf", () => {
    expect(title(item("c", "DS-9", { title: "Well data" }))).toBe("Well data");
    expect(title(item("c", "DS-9"))).toBe("DS-9");
    expect(series(item("c", "DS-9", { title: "Well data" }))).toBe("DS-9");
    expect(collectionLabel("ugs-publications/B")).toBe("B");
    expect(collectionRoot("ugs-serving-topics/emp")).toBe("ugs-serving-topics");
  });

  it("typeOf prefers pub_type, then topic, then series", () => {
    expect(typeOf(item("c", "1", { "ugs:pub_type": "Geologic Map", "ugs:series": "GQ" }))).toBe("Geologic Map");
    expect(typeOf(item("c", "1", { "ugs:topic": "hazards", "ugs:series": "X" }))).toBe("hazards");
    expect(typeOf(item("c", "1", { "ugs:series": "DS" }))).toBe("DS");
    expect(typeOf(item("c", "1"))).toBe("");
  });

  it("scale / author read their ugs: keys", () => {
    expect(scale(item("c", "1", { "ugs:scale": "1:24,000" }))).toBe("1:24,000");
    expect(author(item("c", "1", { "ugs:author": "Doe, J." }))).toBe("Doe, J.");
  });

  it("dateOf slices the ISO datetime; year parses it; fmtDate shows real precision", () => {
    expect(dateOf(item("c", "1", { datetime: "2023-05-01T00:00:00Z" }))).toBe("2023-05-01");
    expect(dateOf(item("c", "1"))).toBe("");
    expect(year(item("c", "1", { datetime: "2023-05-01T00:00:00Z" }))).toBe(2023);
    expect(year(item("c", "1"))).toBeNull();
    expect(fmtDate("2021-01-01")).toBe("2021");       // year-only placeholder
    expect(fmtDate("2021-09-01")).toBe("Sep 2021");   // month precision
    expect(fmtDate("2021-09-14")).toBe("2021-09-14"); // full date
    expect(fmtDate("")).toBe("");
  });

  it("hasGeometry reads a valid bbox, rejects a missing one", () => {
    expect(hasGeometry(item("c", "1", {}, { bbox: [-114, 37, -109, 42] }))).toBe(true);
    expect(hasGeometry(item("c", "1"))).toBe(false);
  });

  it("rowCount / recordCountLabel only when a count is published", () => {
    expect(rowCount(item("c", "1", { "ugs:row_count": 810 }))).toBe(810);
    expect(rowCount(item("c", "1"))).toBeUndefined();
    expect(recordCountLabel(item("c", "1", { "ugs:row_count": 810 }))).toBe("810 rows");
    expect(recordCountLabel(item("c", "1", { "ugs:row_count": 1 }))).toBe("1 row");
    expect(recordCountLabel(item("c", "1"))).toBeUndefined();
  });
});

describe("itemKind / kindLabel", () => {
  it("a publication root reads as a publication", () => {
    expect(itemKind(item("ugs-publications/DS", "DS-9", { "ugs:series": "DS" }))).toBe("publication");
    expect(kindLabel(item("ugs-publications/DS", "DS-9"))).toBe("Publication");
  });
  it("a dbt_schema topic reads as vector data", () => {
    expect(itemKind(item("ugs-serving-topics/emp", "wells", { "ugs:dbt_schema": "emp" }))).toBe("vector");
    expect(kindLabel(item("ugs-serving-topics/emp", "wells", { "ugs:dbt_schema": "emp" }))).toBe("Vector data");
  });
  it("a COG asset reads as raster; an unclassifiable item is Dataset", () => {
    expect(itemKind(item("ugs-rasters/x", "dem", {}, { assets: { data: { href: "x.cog.tif" } } }))).toBe("raster");
    expect(kindLabel(item("ugs-other/x", "misc"))).toBe("Dataset");
  });
});

describe("categorize", () => {
  it("maps a dbt_schema to its topic category (first match wins)", () => {
    expect(categorize(item("ugs-serving-topics/emp", "wells", { "ugs:dbt_schema": "emp" })))
      .toEqual({ key: "energy-minerals", label: "Energy & Minerals" });
    expect(categorize(item("ugs-serving-topics/hazards", "qf", { "ugs:dbt_schema": "hazards" })).label).toBe("Hazards");
  });
  it("maps the top collections by collId root", () => {
    expect(categorize(item("ugs-rasters/x", "dem")).key).toBe("rasters");
    expect(categorize(item("ugs-geologic-maps", "gm")).key).toBe("geologic-maps");
    expect(categorize(item("ugs-mining-district-files/x", "m")).key).toBe("mining-district-files");
  });
  it("falls back to Publications, then Other", () => {
    expect(categorize(item("ugs-publications/DS", "DS-9", { "ugs:series": "DS" })).key).toBe("publications");
    expect(categorize(item("ugs-external/x", "z")).key).toBe("publications");
    expect(categorize(item("ugs-flux/x", "z")).key).toBe("other");
  });
  it("categoryLabel resolves a key back to its label", () => {
    expect(categoryLabel("hazards")).toBe("Hazards");
    expect(categoryLabel("other")).toBe("Other");
    expect(categoryLabel("mystery")).toBe("mystery");
  });
});

describe("formatsOf", () => {
  it("buckets assets by kind and adds PMTiles from a web-map link, skipping thumbnails", () => {
    const it = item("c", "1", {}, {
      assets: {
        data: { href: "x.parquet", type: "application/x-parquet" },
        pub: { href: "x.pdf", type: "application/pdf" },
        thumb: { href: "t.png", type: "image/png", roles: ["thumbnail"] },
      },
      links: [{ rel: "pmtiles", href: "x.pmtiles" }],
    });
    expect(formatsOf(it).sort()).toEqual(["GeoParquet", "PMTiles", "PDF"].sort());
  });
  it("is empty for a bare item", () => {
    expect(formatsOf(item("c", "1"))).toEqual([]);
  });
});

describe("bylineParts", () => {
  it("joins imprint · author · scale · date, only present parts", () => {
    expect(bylineParts(item("c", "1", {
      "ugs:pub_type": "Geologic Map", "ugs:series_id": "GQ-1560", "ugs:author": "Doe, J.",
      "ugs:scale": "1:24,000", datetime: "2020-01-01T00:00:00Z",
    }))).toEqual(["Geologic Map GQ-1560", "Doe, J.", "Scale 1:24,000", "2020"]);
    expect(bylineParts(item("c", "1"))).toEqual([]);
  });
});

describe("curatedDerived", () => {
  it("splits present keys into curated vs derived, dropping absent/empty ones", () => {
    const { curated, derived } = curatedDerived({
      "ugs:author": "Doe, J.", "ugs:pub_type": "Report", "ugs:scale": "",
      "ugs:dbt_schema": "emp", "ugs:row_count": 1234, datetime: "2024-03-01T00:00:00Z",
      "ugs:renders": { default: {} }, description: "ignored here",
    });
    expect(curated).toEqual([
      { label: "Author", value: "Doe, J." },
      { label: "Publication type", value: "Report" },
    ]);
    expect(derived).toEqual([
      { label: "Schema", value: "emp" },
      { label: "Records", value: "1,234" },
      { label: "Ingested", value: "Mar 2024" },
    ]);
  });
  it("is empty for empty props", () => {
    expect(curatedDerived({})).toEqual({ curated: [], derived: [] });
  });
});
