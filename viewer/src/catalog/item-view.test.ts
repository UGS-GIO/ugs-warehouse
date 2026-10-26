import { describe, expect, it } from "vitest";
import type { ItemRef } from "./browse";
import {
  author, bylineParts, categorize, categoryLabel, collectionLabel, collectionRoot, curatedDerived, seriesLabel,
  dateOf, fmtDate, formatBadgeAssets, formatsOf, isSpatialData, itemKind, kindLabel, recordCountLabel, rowCount, scale,
  assetEntries, groupParts, groupPartsByFile, PART_GROUP_OPEN, plateEntry, previewKindOf, series, title, typeOf, year,
  parseScale, scaleDenominator, firstAuthor,
} from "./item-view";
import { collectionAsItem, hasFootprint, type StacDoc } from "@/stac";

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

  it("hasFootprint reads a valid bbox, rejects a missing one", () => {
    expect(hasFootprint(item("c", "1", {}, { bbox: [-114, 37, -109, 42] }).data)).toBe(true);
    expect(hasFootprint(item("c", "1").data)).toBe(false);
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
  });

  it("files the mapping serving tables and the geologic-map mosaics under ONE category", () => {
    // geolmap_geolunits_500k (a mapping serving table) and geologic-maps-500k (the seamless tile
    // layer of the same 1:500k units) used to land in two categories a letter apart.
    const table = item("ugs-serving-topics/mapping", "geolmap_geolunits_500k", { "ugs:dbt_schema": "mapping" });
    const mosaic = item("ugs-geologic-maps", "geologic-maps-500k");
    expect(categorize(table).key).toBe(categorize(mosaic).key);
    expect(categorize(table).label).toBe("Geologic Maps");
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
  it("skips a metadata record, the way it skips a thumbnail", () => {
    const it = item("c", "1", {}, { assets: {
      pub: { href: "x.pdf", type: "application/pdf" },
      metadata: { href: "x.xml", type: "application/xml", roles: ["metadata"], title: "ISO 19139 metadata" },
    } });
    expect(formatsOf(it)).toEqual(["PDF"]);
  });
});

// A publication layer Item carries a `pmtiles` asset (role visual) and a `rel=pmtiles` link naming
// its tile layer, as a serving topic does, with no `ugs:renders` style; a plate Item carries a COG.
const COG = "image/tiff; application=geotiff; profile=cloud-optimized";
const pubLayer: StacDoc = {
  type: "Feature", id: "M-246DM__UtahLandslideMaps__DebrisFlowPaths",
  properties: { title: "Debris Flow Paths", "ugs:series_id": "M-246DM" },
  assets: {
    data: { href: "https://x/vectors/M-246DM/UtahLandslideMaps__DebrisFlowPaths.parquet", type: "application/vnd.apache.parquet", roles: ["data"] },
    pmtiles: { href: "https://x/vectors/M-246DM/UtahLandslideMaps__DebrisFlowPaths.pmtiles", type: "application/vnd.pmtiles", roles: ["visual"] },
  },
  links: [{ rel: "pmtiles", href: "https://x/vectors/M-246DM/UtahLandslideMaps__DebrisFlowPaths.pmtiles",
            type: "application/vnd.pmtiles", "pmtiles:layers": ["UtahLandslideMaps__DebrisFlowPaths"] }],
};
const plate: StacDoc = {
  type: "Feature", id: "M-246DM_plate", properties: { title: "Map plate" },
  assets: {
    cog: { href: "https://x/M-246DM.cog.tif", type: COG, roles: ["data", "cloud-optimized"], "proj:code": "EPSG:3857" },
    thumbnail: { href: "https://x/M-246DM.webp", type: "image/webp", roles: ["thumbnail"] },
  },
};

describe("previewKindOf", () => {
  it("gives a publication layer with PMTiles the vector preview, like a serving topic", () => {
    expect(previewKindOf(pubLayer)).toBe("vector");
    expect(previewKindOf({ ...pubLayer, links: [] })).toBe("assets");
    expect(previewKindOf({ type: "Collection", assets: { publication: { href: "x.pdf", type: "application/pdf" } } }))
      .toBe("assets");
  });
});

describe("the publication's asset tabs", () => {
  const pubAssets: StacDoc = { type: "Collection", id: "M-246DM", assets: {
    thumbnail: { href: "c.webp", type: "image/webp", roles: ["thumbnail"] },
    gis_data_zip: { href: "a.zip", type: "application/zip", roles: ["source"] },
    publication: { href: "m.pdf", type: "application/pdf", title: "Publication" },
  } };

  it("adds the plate's COG as a Map tab, the PDF staying first", () => {
    const tabs = assetEntries(pubAssets, plateEntry(plate)).filter((e) => e.kind !== "other");
    expect(tabs.map((e) => [e.key, e.kind])).toEqual([["publication", "pdf"], ["plate", "cog"]]);
    const tab = tabs[1];
    expect(tab.asset.href).toBe("https://x/M-246DM.cog.tif");
    expect(tab.asset.title).toBe("Map plate");
    expect(tab.doc).toBe(plate);   // drawn with the plate Item, for its projection
  });

  it("has no Map tab without a plate (a report, or before the plate loads)", () => {
    expect(plateEntry(undefined)).toBeUndefined();
    expect(plateEntry({ type: "Feature", id: "x_plate", assets: {} })).toBeUndefined();
    expect(assetEntries(pubAssets).map((e) => e.key)).toEqual(["publication", "gis_data_zip"]);
  });
});

describe("overview parquets stay out of the asset lists", () => {
  const overview = { href: "x_overview_1.parquet", type: "application/vnd.apache.parquet", roles: ["ugs:overview"] };
  const data = { href: "x.parquet", type: "application/vnd.apache.parquet", roles: ["data"] };
  const doc = { type: "Feature", id: "x", assets: { data, overview_1: overview } } as unknown as StacDoc;

  it("are not an asset tab", () => {
    expect(assetEntries(doc).map((e) => e.key)).toEqual(["data"]);
  });

  it("are not a format chip or facet", () => {
    const ref = { collId: "c", id: "x", data: doc } as unknown as ItemRef;
    expect(formatBadgeAssets(ref).map(([k]) => k)).toEqual(["data"]);
    expect(formatsOf({ ...ref, data: { ...doc, assets: { overview_1: overview } } } as unknown as ItemRef)).toEqual([]);
  });
});

describe("footprints and spatial data", () => {
  it("does not count the statewide placeholder as a footprint", () => {
    const onUtah = item("ugs-publications/C", "C-139", { "ugs:footprint_source": "utah-bbox" },
      { bbox: [-114.05, 37, -109.04, 42] });
    expect(hasFootprint(onUtah.data)).toBe(false);
    expect(hasFootprint(item("c", "1", { "ugs:footprint_source": "cog" }, { bbox: [-112, 40, -111, 41] }).data))
      .toBe(true);
  });
  it("treats null geometry, or a schema with no geometry column, as a plain table", () => {
    const cols = (c: { name: string; type?: string }[]) => ({ data: { href: "x.parquet", "table:columns": c } });
    expect(isSpatialData({ geometry: null })).toBe(false);
    expect(isSpatialData({ geometry: { type: "Point", coordinates: [0, 0] },
      assets: cols([{ name: "MapUnit", type: "string" }]) })).toBe(false);
    expect(isSpatialData({ assets: cols([{ name: "MapUnit" }, { name: "geometry", type: "geometry" }]) })).toBe(true);
    expect(isSpatialData({ assets: cols([{ name: "geom", type: "blob" }]) })).toBe(true);
    // No schema published: assume spatial, as before.
    expect(isSpatialData({ geometry: { type: "Point", coordinates: [0, 0] }, assets: {} })).toBe(true);
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

// The publication page renders a publication Collection through the same getters, once
// collectionAsItem has lifted its top-level metadata into properties.
describe("a publication Collection on the item page", () => {
  const coll = collectionAsItem({
    type: "Collection", id: "OFR-9", title: "Geologic map of somewhere", description: "Citation.",
    "ugs:series_id": "OFR-9", "ugs:pub_type": "Open-File Report", "ugs:author": "Doe, J.",
    extent: { spatial: { bbox: [[-112, 40, -111, 41]] }, temporal: { interval: [["2020-06-01T00:00:00Z", null]] } },
    assets: { publication: { href: "https://x/ofr-9.pdf", type: "application/pdf" } },
  } as StacDoc);
  const it_: ItemRef = { collId: "ugs-publications/OFR", href: "", data: coll };

  it("reads title, byline, kind, format and published date", () => {
    expect(title(it_)).toBe("Geologic map of somewhere");
    expect(seriesLabel(it_)).toBe("OFR-9");
    expect(bylineParts(it_)).toEqual(["Open-File Report OFR-9", "Doe, J.", "Jun 2020"]);
    expect(kindLabel(it_)).toBe("Publication");
    expect(categorize(it_).key).toBe("publications");
    expect(formatsOf(it_)).toEqual(["PDF"]);
    expect(hasFootprint(it_.data)).toBe(true);
    const { curated } = curatedDerived(coll.properties ?? {});
    expect(curated.find((r) => r.label === "Published")?.value).toBe("Jun 2020");
  });

  it("shows no date for an undated publication", () => {
    const undated = collectionAsItem({ ...coll, type: "Collection", properties: undefined,
      title: "Undated", "ugs:series_id": "OFR-10", "ugs:date_unknown": true,
      extent: { temporal: { interval: [["1850-01-01T00:00:00Z", "2026-01-01T00:00:00Z"]] } } } as StacDoc);
    expect(dateOf({ collId: "ugs-publications/OFR", href: "", data: undated })).toBe("");
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
  it("labels a publication's date Published, not Ingested", () => {
    expect(curatedDerived({
      "ugs:series_id": "OFR-771", "ugs:pub_type": "Open File Report", datetime: "2025-01-01T00:00:00Z",
    })).toEqual({
      curated: [
        { label: "Publication type", value: "Open File Report" },
        { label: "Series ID", value: "OFR-771" },
        { label: "Published", value: "2025" },
      ],
      derived: [],
    });
  });
  it("does not count an empty series ID as a publication", () => {
    expect(curatedDerived({ "ugs:series_id": "", datetime: "2026-09-12T18:03:00Z" })).toEqual({
      curated: [],
      derived: [{ label: "Ingested", value: "2026-09-12" }],
    });
  });
});

describe("seriesLabel", () => {
  it("keeps a publication id — it is the citation, not the title repeated", () => {
    expect(seriesLabel(item("ugs-publications/DS", "DS-9", { title: "Geologic map of the Wasatch" })))
      .toBe("DS-9");
  });

  it("drops an id that only prefixes the title", () => {
    // The card used to print geolmap_strat_columns_geologic_history_book ABOVE
    // strat_columns_geologic_history_book, in a heavier weight than the title itself.
    expect(seriesLabel(item("ugs-serving-topics/mapping", "geolmap_strat_columns_geologic_history_book",
      { title: "strat_columns_geologic_history_book" }))).toBeUndefined();
  });

  it("drops an id equal to the title, ignoring punctuation and case", () => {
    expect(seriesLabel(item("ugs-serving-topics/emp", "enmin_ucrc_wells", { title: "Enmin UCRC Wells" })))
      .toBeUndefined();
  });

  it("drops an id that only adds the schema prefix — the meta line already names the schema", () => {
    expect(seriesLabel(item("ugs-serving-topics/emp", "enmin_ucrc_wells", { title: "UCRC Wells" })))
      .toBeUndefined();
  });

  it("keeps an id carrying something the title does not say", () => {
    expect(seriesLabel(item("ugs-serving-topics/hazards", "qfaults_2024", { title: "Quaternary Faults" })))
      .toBe("qfaults_2024");
  });
});

describe("parseScale / scaleDenominator", () => {
  it("reads the plain forms a unit layer or an old catalog carries", () => {
    expect([parseScale("1:24,000"), parseScale("1 : 24 000"), parseScale("24000"), parseScale("100k"),
      parseScale("1 inch = 200 feet")]).toEqual([24000, 24000, 24000, 100000, 2400]);
    expect([parseScale("unk"), parseScale("0.25"), parseScale(""), parseScale(undefined)])
      .toEqual([undefined, undefined, undefined, undefined]);
  });
  it("prefers the ingest's number over the raw text", () => {
    expect(scaleDenominator(item("ugs-publications/M", "M-1", { "ugs:scale": "1:24,000", "ugs:scale_denominator": 100000 })))
      .toBe(100000);
    expect(scaleDenominator(item("ugs-publications/M", "M-2", { "ugs:scale": "1:62,500" }))).toBe(62500);
    expect(scaleDenominator(item("ugs-publications/M", "M-3"))).toBeUndefined();
  });
});

describe("firstAuthor", () => {
  const by = (a: string) => firstAuthor(item("ugs-publications/M", "M-1", { "ugs:author": a }));
  it("takes the first surname and marks co-authors", () => {
    expect(by("Larry M. Trimble; Hellmut H. Doelling")).toBe("Trimble et al.");
    expect(by("William L. Stokes")).toBe("Stokes");
    expect(by("Sanchez, J.D.; Hayes, P.T.")).toBe("Sanchez et al.");
    expect(by("Thomas C. Chidsey, Jr")).toBe("Chidsey");
    expect(by("Robert P. Fischer Jr.")).toBe("Fischer");
    expect(by("")).toBe("");
  });
});

describe("groupParts (the publication page's layers and tables)", () => {
  const map = [{ id: "P_plate", title: "Map plate" }, { id: "P_units", title: "Geologic unit polygons" }];
  const of = (source: string, kind: string, n: number) =>
    Array.from({ length: n }, (_, i) => ({ id: `${source}-${kind}-${i}`, title: `${kind} ${i}`, kind, source }));

  it("is one flat list when the links carry no kind (older publications)", () => {
    const parts = [...map, { id: "P__x", title: "X" }];
    expect(groupParts(parts)).toEqual([{ count: 3, collapsed: false, groups: [{ parts, collapsed: false }] }]);
    expect(groupParts([])).toEqual([]);
  });

  it("puts the plate, units and fence first under Map, then kinds in order, for one source", () => {
    const parts = [...of("Mills", "supporting", 1), ...of("Mills", "table", 2), ...map, ...of("Mills", "layer", 3)];
    const sections = groupParts(parts);
    expect(sections.map((s) => s.label)).toEqual([undefined, undefined]);
    expect(sections[0].groups.map((g) => [g.label, g.parts.map((p) => p.id)])).toEqual([["Map", ["P_plate", "P_units"]]]);
    expect(sections[1].groups.map((g) => [g.label, g.parts.length])).toEqual(
      [["Map layers", 3], ["Tables", 2], ["Supporting tables", 1]]);
  });

  it("groups by source dataset first when there are several (quads), then by kind", () => {
    const sections = groupParts([...map, ...of("Quad B", "layer", 2), ...of("Quad A", "layer", 1), ...of("Quad A", "table", 1)]);
    expect(sections.map((s) => [s.label, s.count])).toEqual([[undefined, 2], ["Quad A", 2], ["Quad B", 2]]);
    expect(sections[1].groups.map((g) => g.label)).toEqual(["Map layers", "Tables"]);
  });

  it(`collapses a group of more than ${PART_GROUP_OPEN} entries, and keeps Map and small groups open`, () => {
    const big = groupParts([...map, ...of("Mills", "layer", PART_GROUP_OPEN + 1), ...of("Mills", "table", 2)]);
    expect(big[0].groups[0].collapsed).toBe(false);
    expect(big[1].groups.map((g) => [g.label, g.collapsed])).toEqual([["Map layers", true], ["Tables", false]]);
    const quads = groupParts([...of("Quad A", "layer", PART_GROUP_OPEN + 1), ...of("Quad B", "layer", 2)]);
    expect(quads.map((s) => [s.label, s.collapsed])).toEqual([["Quad A", true], ["Quad B", false]]);
  });

  it("keeps two raw sources apart when their titles read the same, and shows the title", () => {
    const tag = (sourceKey: string) => (p: ReturnType<typeof of>[number]) => ({ ...p, id: `${sourceKey}-${p.id}`, sourceKey });
    const sections = groupParts([...of("Mills Junction", "layer", 2).map(tag("MillsJunction")),
                                 ...of("Mills Junction", "layer", 1).map(tag("Mills_Junction"))]);
    expect(sections.map((s) => [s.label, s.count])).toEqual([["Mills Junction", 2], ["Mills Junction", 1]]);
    expect(sections[1].groups[0].parts[0].id).toMatch(/^Mills_Junction-/);
  });

  it("groups by the title when the links carry no raw source key", () => {
    expect(groupParts([...of("Quad A", "layer", 1), ...of("Quad B", "layer", 1)]).map((s) => s.label)).toEqual(["Quad A", "Quad B"]);
  });

  it("files an unknown kind after the known ones under its own name", () => {
    expect(groupParts(of("Mills", "annotation", 1).concat(of("Mills", "layer", 1)))[0].groups.map((g) => g.label))
      .toEqual(["Map layers", "annotation"]);
  });
});

describe("groupPartsByFile (source file, then dataset, then kind)", () => {
  const part = (id: string, kind: string, sourceFile?: string, source = "Geo") =>
    ({ id, title: id, kind, source, ...(sourceFile ? { sourceFile } : {}) });
  const plate = { id: "P_plate", title: "Map plate" };

  it("groups by file, keeps the plate on top, and nests dataset then kind", () => {
    const zip = "GIS Data - Zip";
    const xl = "Appendix A - Excel";
    const out = groupPartsByFile([plate, part("a", "layer", zip, "Quad A"), part("b", "table", zip, "Quad B"),
      part("c", "table", xl), part("d", "layer", zip, "Quad A")]);
    expect(out.map((f) => [f.label, f.count])).toEqual([[undefined, 1], [zip, 3], [xl, 1]]);
    expect(out[0].sections[0].groups[0].label).toBe("Map");
    // The zip holds two datasets; the Excel file has one, so its dataset level collapses.
    expect(out[1].sections.map((s) => s.label)).toEqual(["Quad A", "Quad B"]);
    expect(out[2].sections.map((s) => s.label)).toEqual([undefined]);
    expect(out[2].sections[0].groups.map((g) => g.label)).toEqual(["Tables"]);
  });

  it("has no file level when every part came from one file", () => {
    const out = groupPartsByFile([part("a", "layer", "GIS Data - Zip"), part("b", "table", "GIS Data - Zip")]);
    expect(out.map((f) => f.label)).toEqual([undefined]);
    expect(out[0].sections[0].groups.map((g) => g.label)).toEqual(["Map layers", "Tables"]);
  });

  it("files parts with no source file under Other files beside the named ones", () => {
    const out = groupPartsByFile([part("a", "layer", "GIS Data - Zip"), part("b", "table")]);
    expect(out.map((f) => f.label)).toEqual(["GIS Data - Zip", "Other files"]);
  });

  it("falls back to groupParts for parts that carry no source file (older catalogs)", () => {
    const parts = [plate, part("a", "layer"), part("b", "table")];
    expect(groupPartsByFile(parts)).toEqual([{ count: 3, collapsed: false, sections: groupParts(parts) }]);
    expect(groupPartsByFile([])).toEqual([]);
  });
});
