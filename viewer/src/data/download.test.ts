import { describe, expect, it } from "vitest";
import { buildOrder, evictionVictim, filterClause, sanitize, shapefileFieldChecks } from "./download";

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
