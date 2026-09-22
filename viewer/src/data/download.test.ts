import { describe, expect, it } from "vitest";
import {
  buildOrder, dbfFieldWidth, estimateExportPeakBytes, estimateGeoJSONBytes, estimateShapefileBytes,
  evictionVictim, featureSeqSql, filterClause, sanitize, wrapFeatureSeqInPlace,
  estimateReadBytes, holdsOneGeomType, needsMeasuredWidth, safeEpsg, shapefileFieldChecks,
  SHP_FILE_LIMIT, baseGeometryTypes, geomColumn, WASM_HEAP_BUDGET,
} from "./download";

describe("sanitize", () => {
  it("keeps safe bigints as numbers", () => {
    expect(sanitize(42n)).toBe(42);
  });
  it("stringifies bigints beyond 2^53 (exact precision)", () => {
    expect(sanitize(9007199254740993n)).toBe("9007199254740993"); // MAX_SAFE_INTEGER + 2
    expect(sanitize(-9007199254740993n)).toBe("-9007199254740993");
  });
  it("iso-formats Dates", () => {
    expect(sanitize(new Date("2026-01-02T03:04:05Z"))).toBe("2026-01-02T03:04:05.000Z");
  });
  it("passes strings / numbers / null through", () => {
    expect(sanitize("x")).toBe("x");
    expect(sanitize(3.14)).toBe(3.14);
    expect(sanitize(null)).toBe(null);
  });
});

describe("shapefileFieldChecks", () => {
  it("flags field names over 10 chars", () => {
    const r = shapefileFieldChecks(["county", "metadata_publication_id", "td_ft"]);
    expect(r.longNames).toEqual(["metadata_publication_id"]);
  });
  it("detects post-truncation collisions (silent data loss)", () => {
    // both truncate to 'elevation_'
    expect(shapefileFieldChecks(["elevation_gl", "elevation_kb"]).collisions)
      .toEqual([["elevation_gl", "elevation_kb"]]);
  });
  it("no collision when the first 10 chars differ", () => {
    expect(shapefileFieldChecks(["county", "current_operator"]).collisions).toEqual([]);
  });
  it("flags more than 255 fields", () => {
    const many = Array.from({ length: 256 }, (_, i) => `f${i}`);
    expect(shapefileFieldChecks(many).tooManyFields).toBe(true);
    expect(shapefileFieldChecks(many).fieldCount).toBe(256);
    expect(shapefileFieldChecks(["a", "b"]).tooManyFields).toBe(false);
  });
});

describe("evictionVictim", () => {
  const reg = () => new Map([["urlA", "q1"], ["urlB", "q2"], ["urlC", "q3"]]); // insertion = LRU order
  it("returns the oldest (LRU) entry when nothing is in use", () => {
    expect(evictionVictim(reg(), new Map())).toEqual(["urlA", "q1"]);
  });
  it("skips in-use handles and returns the oldest free one", () => {
    expect(evictionVictim(reg(), new Map([["q1", 1]]))).toEqual(["urlB", "q2"]);
    expect(evictionVictim(reg(), new Map([["q1", 1], ["q2", 2]]))).toEqual(["urlC", "q3"]);
  });
  it("returns undefined when every handle is in use (caller runs over cap)", () => {
    expect(evictionVictim(reg(), new Map([["q1", 1], ["q2", 1], ["q3", 1]]))).toBeUndefined();
  });
});

describe("filterClause", () => {
  it("builds an exact-match predicate for the related-table join (relate_id = value)", () => {
    // The clicked feature's join value drives the related lookup: an equality, not a range/contains.
    expect(filterClause({ col: "relate_id", kind: "exact", value: "FL-3" }))
      .toBe(`CAST("relate_id" AS VARCHAR) = 'FL-3'`);
  });

  it("escapes a single quote in the exact value (no SQL injection via the join key)", () => {
    expect(filterClause({ col: "relate_id", kind: "exact", value: "O'Brien" }))
      .toBe(`CAST("relate_id" AS VARCHAR) = 'O''Brien'`);
  });

  it("compares as text so a numeric join key still matches", () => {
    expect(filterClause({ col: "flhhazardunit", kind: "exact", value: "42" }))
      .toBe(`CAST("flhhazardunit" AS VARCHAR) = '42'`);
  });

  it("still builds range and contains predicates", () => {
    expect(filterClause({ col: "td", kind: "number", min: 10, max: 20 }))
      .toBe(`"td" >= 10 AND "td" <= 20`);
    expect(filterClause({ col: "name", kind: "text", contains: "fault" }))
      .toBe(`CAST("name" AS VARCHAR) ILIKE '%fault%'`);
  });
});

// ~35MB of engine: our build only, so an outage elsewhere cannot break export.
describe("duckdb-wasm is self-hosted", () => {
  it("no source file selects the jsDelivr bundle", async () => {
    const { readFileSync, readdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    const walk = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory() ? walk(join(dir, e.name))
          : /\.(ts|tsx)$/.test(e.name) ? [join(dir, e.name)] : []);
    // Usage, not the word in a comment.
    const offenders = walk("src")
      .filter((f) => !f.endsWith("download.test.ts"))
      .filter((f) => /getJsDelivrBundles\s*\(|jsdelivr\.net/i.test(readFileSync(f, "utf8")));

    expect(offenders).toEqual([]);
  });
});

describe("buildOrder", () => {
  const cols = ["acres", "attribute"];

  it("sorts by nothing when the user has not picked a column", () => {
    expect(buildOrder(cols, { limit: 50, offset: 0 }, true)).toBe("");
  });

  it("ignores an orderBy that is not a displayed column", () => {
    expect(buildOrder(cols, { limit: 50, offset: 0, orderBy: "geom" }, true)).toBe("");
  });

  it("appends the feature_id tiebreaker under a user sort", () => {
    expect(buildOrder(cols, { limit: 50, offset: 0, orderBy: "acres" }, true))
      .toBe(' ORDER BY "acres" ASC NULLS LAST, "feature_id" ASC');
  });

  it("omits the tiebreaker when the parquet has no feature_id", () => {
    expect(buildOrder(cols, { limit: 50, offset: 0, orderBy: "acres", desc: true }, false))
      .toBe(' ORDER BY "acres" DESC NULLS LAST');
  });
});

describe("featureSeqSql", () => {
  it("emits one GeoJSON Feature per row, geometry and properties as JSON (not escaped strings)", () => {
    expect(featureSeqSql(["name", "td"], "geom", "raw")).toBe(
      `SELECT 'Feature' AS type, ST_AsGeoJSON(geom)::JSON AS geometry, `
      + `to_json({'name': "name", 'td': "td"}) AS properties FROM raw`);
  });
  it("escapes quotes in column names", () => {
    expect(featureSeqSql(["O'Brien"], "geom", "raw")).toContain(`'O''Brien': "O'Brien"`);
  });
  it("still produces valid features when the table has no attribute columns", () => {
    expect(featureSeqSql([], "geom", "raw")).toContain(`'{}'::JSON AS properties`);
  });
});

describe("wrapFeatureSeqInPlace", () => {
  const dec = new TextDecoder();
  const wrap = (s: string) =>
    wrapFeatureSeqInPlace(new TextEncoder().encode(s)).map((p) => dec.decode(p)).join("");

  it("joins newline-delimited features into one FeatureCollection", () => {
    expect(JSON.parse(wrap('{"type":"Feature","id":1}\n{"type":"Feature","id":2}\n')))
      .toEqual({ type: "FeatureCollection", features: [{ type: "Feature", id: 1 }, { type: "Feature", id: 2 }] });
  });
  it("handles a missing trailing newline and a single feature", () => {
    expect(JSON.parse(wrap('{"id":1}')).features).toEqual([{ id: 1 }]);
  });
  it("yields an empty collection for no rows", () => {
    expect(JSON.parse(wrap("")).features).toEqual([]);
  });
  it("returns Blob parts rather than reallocating the payload", () => {
    // A second full copy is the allocation a large GeoJSON export cannot afford, so the middle
    // part must be a view onto the caller's buffer.
    const seq = new TextEncoder().encode('{"id":1}\n{"id":2}\n');
    const parts = wrapFeatureSeqInPlace(seq);
    expect(parts).toHaveLength(3);
    expect(parts[1].buffer).toBe(seq.buffer);
  });
  it("leaves escaped newlines inside string values alone", () => {
    // The writer escapes a real newline as \\n, so only record separators are raw 0x0A.
    expect(JSON.parse(wrap('{"note":"a\\nb"}\n')).features[0].note).toBe("a\nb");
  });
});

describe("dbfFieldWidth", () => {
  it("uses fixed widths for typed columns", () => {
    expect(dbfFieldWidth("BOOLEAN", 0)).toBe(1);
    expect(dbfFieldWidth("INTEGER", 0)).toBe(11);
    expect(dbfFieldWidth("BIGINT", 0)).toBe(20);
    expect(dbfFieldWidth("DOUBLE", 0)).toBe(24);
    expect(dbfFieldWidth("DATE", 0)).toBe(8);
    expect(dbfFieldWidth("TIMESTAMP WITH TIME ZONE", 0)).toBe(24);
  });
  it("sizes VARCHAR to its longest decoded value and caps at the dbf 254 maximum", () => {
    expect(dbfFieldWidth("VARCHAR", 44)).toBe(44);
    expect(dbfFieldWidth("VARCHAR", 5000)).toBe(254);
    expect(dbfFieldWidth("VARCHAR", 0)).toBe(1);
  });
  it("marks only the text types as needing a measured width", () => {
    expect(needsMeasuredWidth("VARCHAR")).toBe(true);
    expect(needsMeasuredWidth("BLOB")).toBe(true);
    expect(["BOOLEAN", "INTEGER", "BIGINT", "DOUBLE", "DATE", "TIMESTAMP"]
      .map(needsMeasuredWidth)).toEqual([false, false, false, false, false, false]);
  });
});

describe("estimateShapefileBytes", () => {
  const fields = [{ type: "DOUBLE", maxBytes: 0 }];   // 24-wide

  it("sizes .shp from the geometry bytes and .dbf from the field widths", () => {
    const r = estimateShapefileBytes(fields, 1000, 500_000);
    expect(r.estShpBytes).toBe(100 + 500_000 + 1000 * 40);
    expect(r.estDbfBytes).toBe(33 + 32 + 1000 * 25);
    expect(r.over2gb).toBe(false);
  });

  it("flags a huge attribute table even when the geometry is small (.dbf has its own 2 GB cap)", () => {
    const wide = Array.from({ length: 200 }, () => ({ type: "DOUBLE", maxBytes: 0 }));
    const r = estimateShapefileBytes(wide, 500_000, 1_000);
    expect(r.estShpBytes).toBeLessThan(SHP_FILE_LIMIT);
    expect(r.estDbfBytes).toBeGreaterThan(SHP_FILE_LIMIT);
    expect(r.over2gb).toBe(true);
  });

  it("flags geometry over the cap on its own", () => {
    expect(estimateShapefileBytes(fields, 10, 3 * 1024 ** 3).over2gb).toBe(true);
  });
});

describe("browser memory ceiling", () => {
  const fields = [{ name: "unit_name", type: "VARCHAR", maxBytes: 44, avgBytes: 20 }];

  it("expands geometry bytes, since GeoJSON spells coordinates out as text", () => {
    expect(estimateGeoJSONBytes([], 1_000, 1_000_000)).toBe(2_500_000 + 1_000 * 100);
  });

  it("charges every row for the field name, not just the value", () => {
    const withField = estimateGeoJSONBytes(fields, 1_000, 0);
    const bare = estimateGeoJSONBytes([], 1_000, 0);
    expect(withField - bare).toBe(1_000 * ("unit_name".length + 20 + 6));
  });

  it("counts the input, the written layer and the zip for a zipped format", () => {
    expect(estimateExportPeakBytes("shp", 100, 50)).toBe(200);
    expect(estimateExportPeakBytes("gdb", 100, 50)).toBe(200);
  });

  it("does not charge a single-file format for a zip it never makes", () => {
    expect(estimateExportPeakBytes("gpkg", 100, 50)).toBe(150);
    expect(estimateExportPeakBytes("fgb", 100, 50)).toBe(150);
  });

  it("charges CSV nothing: DuckDB streams it without GeoJSON or GDAL", () => {
    expect(estimateExportPeakBytes("csv", 1e9, 1e9)).toBe(0);
  });

  it("trips below the 2 GB format cap — the browser gives out first", () => {
    // 200k polygons averaging 4 KB of WKB: both shapefile parts fit, the conversion does not.
    const polys = Array.from({ length: 10 }, (_, i) => ({ name: `f${i}`, type: "DOUBLE", maxBytes: 0, avgBytes: 8 }));
    const geomBytes = 200_000 * 4_000;
    const shp = estimateShapefileBytes(polys, 200_000, geomBytes);
    const peak = estimateExportPeakBytes("shp", estimateGeoJSONBytes(polys, 200_000, geomBytes),
      shp.estShpBytes + shp.estDbfBytes);
    expect(shp.over2gb).toBe(false);
    expect(peak).toBeGreaterThan(WASM_HEAP_BUDGET);
  });

  it("leaves an ordinary topic well under the budget", () => {
    const shp = estimateShapefileBytes(fields, 7_000, 3_000_000);
    expect(estimateExportPeakBytes("shp", estimateGeoJSONBytes(fields, 7_000, 3_000_000),
      shp.estShpBytes + shp.estDbfBytes)).toBeLessThan(WASM_HEAP_BUDGET);
  });
});

describe("browser memory ceiling", () => {
  const fields = [{ name: "unit_name", type: "VARCHAR", maxBytes: 44, avgBytes: 20 }];

  it("expands geometry bytes, since GeoJSON spells coordinates out as text", () => {
    expect(estimateGeoJSONBytes([], 1_000, 1_000_000)).toBe(2_500_000 + 1_000 * 100);
  });

  it("charges every row for the field name, not just the value", () => {
    const withField = estimateGeoJSONBytes(fields, 1_000, 0);
    const bare = estimateGeoJSONBytes([], 1_000, 0);
    expect(withField - bare).toBe(1_000 * ("unit_name".length + 20 + 6));
  });

  it("counts the input, the written layer and the zip for a zipped format", () => {
    expect(estimateExportPeakBytes("shp", 100, 50)).toBe(200);
    expect(estimateExportPeakBytes("gdb", 100, 50)).toBe(200);
  });

  it("does not charge a single-file format for a zip it never makes", () => {
    expect(estimateExportPeakBytes("gpkg", 100, 50)).toBe(150);
    expect(estimateExportPeakBytes("fgb", 100, 50)).toBe(150);
  });

  it("charges CSV nothing: DuckDB streams it without GeoJSON or GDAL", () => {
    expect(estimateExportPeakBytes("csv", 1e9, 1e9)).toBe(0);
  });

  it("trips below the 2 GB format cap — the browser gives out first", () => {
    // 200k polygons averaging 4 KB of WKB: both shapefile parts fit, the conversion does not.
    const polys = Array.from({ length: 10 }, (_, i) => ({ name: `f${i}`, type: "DOUBLE", maxBytes: 0, avgBytes: 8 }));
    const geomBytes = 200_000 * 4_000;
    const shp = estimateShapefileBytes(polys, 200_000, geomBytes);
    const peak = estimateExportPeakBytes("shp", estimateGeoJSONBytes(polys, 200_000, geomBytes),
      shp.estShpBytes + shp.estDbfBytes);
    expect(shp.over2gb).toBe(false);
    expect(peak).toBeGreaterThan(WASM_HEAP_BUDGET);
  });

  it("leaves an ordinary topic well under the budget", () => {
    const shp = estimateShapefileBytes(fields, 7_000, 3_000_000);
    expect(estimateExportPeakBytes("shp", estimateGeoJSONBytes(fields, 7_000, 3_000_000),
      shp.estShpBytes + shp.estDbfBytes)).toBeLessThan(WASM_HEAP_BUDGET);
  });
});

// DuckDB-WASM has no range reads: reaching the footer downloads the whole file. The gate has to
// answer from the Content-Length alone, without ever starting the engine.
describe("safeEpsg", () => {
  // The panel's number input yields 0 when cleared and NaN from a partial entry; either would
  // reach SQL as EPSG:0 / EPSG:NaN and fail the transform.
  it.each([0, -1, Number.NaN, 4326.5])("falls back to 4326 for %s", (bad) => {
    expect(safeEpsg(bad)).toBe(4326);
  });
  it("keeps a real projected CRS", () => {
    expect(safeEpsg(26912)).toBe(26912);
  });
});

describe("holdsOneGeomType", () => {
  it.each(["shp", "gdb"] as const)("%s needs the mixed-geometry check", (fmt) => {
    expect(holdsOneGeomType(fmt)).toBe(true);
  });
  it.each(["gpkg", "fgb", "geojson", "csv"] as const)("%s does not", (fmt) => {
    expect(holdsOneGeomType(fmt)).toBe(false);
  });
});

describe("baseGeometryTypes", () => {
  it("dedupes GeoParquet's spaced dimension suffix against the plain name", () => {
    // GeoParquet writes "Point Z"; ST_GeometryType writes "ST_Point". Both are one base type.
    expect(baseGeometryTypes(["Point", "Point Z"])).toEqual(["POINT"]);
    expect(baseGeometryTypes(["ST_MultiPolygon", "Polygon Z M"])).toEqual(["POLYGON"]);
  });
  it("still reports genuinely mixed types", () => {
    expect(baseGeometryTypes(["Point", "LineString"])).toEqual(["POINT", "LINESTRING"]);
  });
});

describe("geomColumn", () => {
  it("finds the geometry column a pub or external parquet actually uses", () => {
    expect(geomColumn(["id", "wkb_geometry"])).toBe("wkb_geometry");
    expect(geomColumn(["id", "geometry"])).toBe("geometry");
  });
  it("prefers our own name and falls back to it", () => {
    expect(geomColumn(["geom", "geometry"])).toBe("geom");
    expect(geomColumn(["id"])).toBe("geom");
  });
});

describe("dictionary-encoded columns", () => {
  it("a repeated string measures its real width, not its parquet page size", () => {
    // 200k rows of two repeated 44-char strings occupy ~54 KB of dictionary-encoded parquet
    // (0.27 bytes/row). Sizing the dbf from that gave 5; the decoded max gives 44.
    const pageSizePerRow = 53_648 / 200_000;
    expect(dbfFieldWidth("VARCHAR", pageSizePerRow)).toBe(1);
    expect(dbfFieldWidth("VARCHAR", 44)).toBe(44);

    const rows = 50_000_000;
    const asPageSize = estimateShapefileBytes([{ type: "VARCHAR", maxBytes: pageSizePerRow }], rows, 0);
    const asMeasured = estimateShapefileBytes([{ type: "VARCHAR", maxBytes: 44 }], rows, 0);
    expect(asPageSize.over2gb).toBe(false);      // the old model waved this through
    expect(asMeasured.over2gb).toBe(true);
  });
});

// What a clip can actually save depends on how finely the file is grouped, not on the AOI size.
// Measured on wetlands_riverine: 2 row groups, both spanning nearly all of Utah, so a 22-feature
// AOI still reads the whole file.
describe("estimateReadBytes", () => {
  const utahWide = { bytes: 1_418_000_000, xmin: -114.98, xmax: -107.31, ymin: 35.73, ymax: 42.79 };
  const second = { ...utahWide, bytes: 263_000_000 };
  const wasatch = { bytes: 50_000_000, xmin: -112.0, xmax: -111.5, ymin: 40.5, ymax: 41.0 };
  const moab = { bytes: 50_000_000, xmin: -110.0, xmax: -109.4, ymin: 38.4, ymax: 38.8 };

  it("reads everything when there is no clip", () => {
    expect(estimateReadBytes([wasatch, moab])).toBe(100_000_000);
  });

  it("reads only the groups an AOI overlaps", () => {
    expect(estimateReadBytes([wasatch, moab], [-111.95, 40.7, -111.85, 40.78])).toBe(50_000_000);
  });

  it("reads nothing when the AOI misses every group", () => {
    expect(estimateReadBytes([wasatch, moab], [-100, 10, -99, 11])).toBe(0);
  });

  it("cannot prune a file stored as two Utah-wide groups, however small the AOI", () => {
    const tiny: [number, number, number, number] = [-111.95, 40.7, -111.85, 40.78];
    expect(estimateReadBytes([utahWide, second], tiny)).toBe(1_681_000_000);
  });

  it("the largest group is the floor on what any clip can read", () => {
    // A row group is the smallest unit a reader can skip.
    const groups = [utahWide, second];
    expect(Math.max(...groups.map((g) => g.bytes))).toBe(1_418_000_000);
  });

  it("counts a group the AOI only touches at the edge", () => {
    expect(estimateReadBytes([wasatch], [-112.5, 40.5, -112.0, 41.0])).toBe(50_000_000);
  });
});
