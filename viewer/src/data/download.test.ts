import { afterEach, describe, expect, it, vi } from "vitest";
import {
  buildOrder, dbfFieldWidth, estimateExportPeakBytes, estimateGeoJSONBytes, estimateShapefileBytes,
  evictionVictim, featureSeqSql, filterClause, sanitize, seqToFeatureCollection,
  shapefileFieldChecks, shapefileWarnings, SHP_FILE_LIMIT, PREFLIGHT_MAX_PARQUET_BYTES,
  WASM_HEAP_BUDGET,
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

describe("seqToFeatureCollection", () => {
  const wrap = (s: string) => new TextDecoder().decode(seqToFeatureCollection(new TextEncoder().encode(s)));

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
  it("estimates VARCHAR from average bytes and caps at the dbf 254 maximum", () => {
    expect(dbfFieldWidth("VARCHAR", 10)).toBe(24);
    expect(dbfFieldWidth("VARCHAR", 5000)).toBe(254);
  });
});

describe("estimateShapefileBytes", () => {
  const fields = [{ type: "DOUBLE", avgBytes: 8 }];   // 24-wide

  it("sizes .shp from the geometry bytes and .dbf from the field widths", () => {
    const r = estimateShapefileBytes(fields, 1000, 500_000);
    expect(r.estShpBytes).toBe(100 + 500_000 + 1000 * 40);
    expect(r.estDbfBytes).toBe(33 + 32 + 1000 * 25);
    expect(r.over2gb).toBe(false);
  });

  it("flags a huge attribute table even when the geometry is small (.dbf has its own 2 GB cap)", () => {
    const wide = Array.from({ length: 200 }, () => ({ type: "DOUBLE", avgBytes: 8 }));
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
  const fields = [{ name: "unit_name", type: "VARCHAR", avgBytes: 20 }];

  it("expands geometry bytes, since GeoJSON spells coordinates out as text", () => {
    expect(estimateGeoJSONBytes([], 1_000, 1_000_000)).toBe(2_500_000 + 1_000 * 100);
  });

  it("charges every row for the field name, not just the value", () => {
    const withField = estimateGeoJSONBytes(fields, 1_000, 0);
    const bare = estimateGeoJSONBytes([], 1_000, 0);
    expect(withField - bare).toBe(1_000 * ("unit_name".length + 20 + 6));
  });

  it("counts the input, the written layer and the zip as live at once", () => {
    expect(estimateExportPeakBytes(100, 50)).toBe(200);
  });

  it("trips below the 2 GB format cap — the browser gives out first", () => {
    // 200k polygons averaging 4 KB of WKB: both shapefile parts fit, the conversion does not.
    const polys = Array.from({ length: 10 }, (_, i) => ({ name: `f${i}`, type: "DOUBLE", avgBytes: 8 }));
    const geomBytes = 200_000 * 4_000;
    const shp = estimateShapefileBytes(polys, 200_000, geomBytes);
    const peak = estimateExportPeakBytes(estimateGeoJSONBytes(polys, 200_000, geomBytes),
      shp.estShpBytes + shp.estDbfBytes);
    expect(shp.over2gb).toBe(false);
    expect(peak).toBeGreaterThan(WASM_HEAP_BUDGET);
  });

  it("leaves an ordinary topic well under the budget", () => {
    const shp = estimateShapefileBytes(fields, 7_000, 3_000_000);
    expect(estimateExportPeakBytes(estimateGeoJSONBytes(fields, 7_000, 3_000_000),
      shp.estShpBytes + shp.estDbfBytes)).toBeLessThan(WASM_HEAP_BUDGET);
  });
});

// DuckDB-WASM has no range reads: reaching the footer downloads the whole file. The gate has to
// answer from the Content-Length alone, without ever starting the engine.
describe("shapefileWarnings size gate", () => {
  const headOnly = (bytes: number) => vi.fn(async (_u: string, init?: RequestInit) => {
    expect(init?.method).toBe("HEAD");
    return { headers: new Headers({ "content-length": String(bytes) }) } as Response;
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  it("refuses a 1.35 GB source on the HEAD alone, without loading DuckDB", async () => {
    const fetchSpy = headOnly(1_351_235_892);
    vi.stubGlobal("fetch", fetchSpy);
    const w = await shapefileWarnings("https://cdn/wetlands_riverine.parquet");
    expect(w.tooBigToInspect).toBe(true);
    expect(w.overBrowserLimit).toBe(true);
    expect(w.any).toBe(true);
    expect(w.sourceBytes).toBe(1_351_235_892);
    expect(w.rowCount).toBe(0);              // nothing was read
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("does not gate a file at the threshold", async () => {
    // Under the cap it falls through to the DuckDB path, which has no engine in this environment.
    vi.stubGlobal("fetch", headOnly(PREFLIGHT_MAX_PARQUET_BYTES));
    await expect(shapefileWarnings("https://cdn/small.parquet")).rejects.toBeTruthy();
  });
});
