import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 5,000 rows in three ZSTD row groups, with a geometry column, bbox columns and feature ids:
// the shape the warehouse publishes (sink_archive).
const FIXTURE = readFileSync(fileURLToPath(new URL("./__fixtures__/small.parquet", import.meta.url)));
const URL_ = "https://cdn.example/small.parquet";

let requested: string[] = [];

beforeEach(() => {
  requested = [];
  vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
    const range = new Headers(init?.headers).get("range");
    if (init?.method === "HEAD") return new Response(null, { headers: { "content-length": String(FIXTURE.length) } });
    const m = range && /bytes=(\d+)-(\d+)/.exec(range);
    requested.push(range ?? "whole file");
    if (!m) return new Response(FIXTURE);
    const [start, end] = [Number(m[1]), Math.min(Number(m[2]), FIXTURE.length - 1)];
    return new Response(FIXTURE.subarray(start, end + 1), {
      status: 206, headers: { "content-range": `bytes ${start}-${end}/${FIXTURE.length}` },
    });
  });
});
afterEach(() => vi.unstubAllGlobals());

async function lite() {
  vi.resetModules();   // a fresh open-file cache per test
  return import("./parquet-lite");
}

describe("parquet-lite", () => {
  it("reads one page in file order, with no geometry and the id kept for linking", async () => {
    const { readPage } = await lite();
    const page = await readPage(URL_, { limit: 3, offset: 2048 });   // the first rows of group 2
    expect(page.total).toBe(5000);
    expect(page.columns).toEqual(["name", "county", "area_km2", "big", "surveyed"]);
    expect(page.rows.map((r) => r.feature_id)).toEqual([2049, 2050, 2051]);
    expect(page.rows[0]).toMatchObject({ name: "fan 2048", big: 2048000 });   // BigInt made a number
    expect(page.rows[0]).not.toHaveProperty("geom");
    expect(page.bboxes[0]?.[0]).toBeCloseTo(-112 + 2048 * 0.0001);
    expect(requested).not.toContain("whole file");
  });

  it("fetches a page's column chunks as merged ranges, not one request per column", async () => {
    const { columnTypes, readPage } = await lite();
    await columnTypes(URL_);   // footer read and cached
    const before = requested.length;
    await readPage(URL_, { limit: 25, offset: 0 });
    // Nine non-geometry columns in row group 1, which sit next to each other: one or two requests.
    expect(requested.length - before).toBeLessThanOrEqual(2);
  });

  it("types numbers and dates as the DuckDB path does", async () => {
    const { columnTypes } = await lite();
    expect(await columnTypes(URL_)).toEqual({ name: "text", county: "text", area_km2: "number", big: "number", surveyed: "text" });
  });

  it("pages a related-rows match, comparing as text", async () => {
    const { readMatching } = await lite();
    const page = await readMatching(URL_, "county", "Davis", { limit: 2, offset: 1 });
    expect(page.total).toBe(1667);   // every third of 5,000
    expect(page.rows.map((r) => r.feature_id)).toEqual([4, 7]);
  });

  it("finds a map-clicked feature's position, in the file or among the matching rows", async () => {
    const { ordinalOf } = await lite();
    expect(await ordinalOf(URL_, 3001)).toBe(3000);
    expect(await ordinalOf(URL_, 7, { col: "county", value: "Davis" })).toBe(2);
    expect(await ordinalOf(URL_, 8, { col: "county", value: "Davis" })).toBeNull();
    expect(await ordinalOf(URL_, 999999)).toBeNull();
  });
});

describe("search scan", () => {
  const opened = async () => {
    const lite = await import("./parquet-lite");
    const { asyncBufferFromUrl } = await import("hyparquet");
    return { lite, o: await lite.openWith(await asyncBufferFromUrl({ url: URL_ })) };
  };

  it("stops at the row group that fills the page, and carries on for a later one", async () => {
    const { lite, o } = await opened();
    const scan = lite.newScan("DAVIS");   // case does not matter; every third row matches
    await lite.scanUntil(o, scan, 25);
    expect(scan.nextGroup).toBe(1);        // row group 1 held 683 matches: enough
    const first = lite.scanPage(o, scan, { limit: 25, offset: 0 });
    expect(first.complete).toBe(false);
    expect(first.rows.map((r) => r.feature_id).slice(0, 3)).toEqual([1, 4, 7]);
    await lite.scanUntil(o, scan, 1000);
    expect(scan.nextGroup).toBe(2);
  });

  it("finishes with the exact total when the term is rare", async () => {
    const { lite, o } = await opened();
    const scan = lite.newScan("fan 4999");
    await lite.scanUntil(o, scan, 25);
    const page = lite.scanPage(o, scan, { limit: 25, offset: 0 });
    expect(page).toMatchObject({ total: 1, complete: true });
    expect(page.rows[0]).toMatchObject({ name: "fan 4999", feature_id: 5000 });
  });

  it("matches numbers as text, as the DuckDB search did", async () => {
    const { lite, o } = await opened();
    const scan = lite.newScan("4998000");   // big = i * 1000
    await lite.scanUntil(o, scan, 25);
    expect(lite.scanPage(o, scan, { limit: 25, offset: 0 }).rows.map((r) => r.feature_id)).toEqual([4999]);
  });

  it.each([[".0", 2500], ["-01-0", 153]])("finds %s in as many rows as DuckDB's text cast", async (term, want) => {
    const { lite, o } = await opened();   // "0.0" for a whole double, "2026-01-01" for a date
    const scan = lite.newScan(term);
    await lite.scanUntil(o, scan, Infinity);
    expect(scan.rows.length).toBe(want);
  });

  it("stops a scan that has been replaced", async () => {
    const { lite, o } = await opened();
    const scan = lite.newScan("nothing matches this");
    await lite.scanUntil(o, scan, 25, () => false);
    expect(scan.nextGroup).toBe(0);
  });
});
