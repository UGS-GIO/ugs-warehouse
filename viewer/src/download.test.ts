import { describe, expect, it } from "vitest";
import { sanitize, shapefileFieldChecks } from "./download";

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
