import { describe, expect, it } from "vitest";
import { CappedMap, lruSet } from "./lru";

describe("lruSet", () => {
  it("evicts the oldest when over cap", () => {
    const c = new Map<string, number>();
    lruSet(c, "a", 1, 2);
    lruSet(c, "b", 2, 2);
    lruSet(c, "c", 3, 2);
    expect([...c.keys()]).toEqual(["b", "c"]);
  });
  it("re-inserting a key makes it most-recently-used (spares it from eviction)", () => {
    const c = new Map<string, number>();
    lruSet(c, "a", 1, 2);
    lruSet(c, "b", 2, 2);
    lruSet(c, "a", 10, 2); // touch a → newest
    lruSet(c, "c", 3, 2);  // evicts b, not a
    expect([...c.keys()]).toEqual(["a", "c"]);
    expect(c.get("a")).toBe(10);
  });
  it("cap of 1 keeps only the newest", () => {
    const c = new Map<string, number>();
    lruSet(c, "a", 1, 1);
    lruSet(c, "b", 2, 1);
    expect([...c.entries()]).toEqual([["b", 2]]);
  });
});

describe("CappedMap", () => {
  it("evicts the oldest on set past cap", () => {
    const m = new CappedMap<string, number>(2);
    m.set("a", 1); m.set("b", 2); m.set("c", 3);
    expect([...m.keys()]).toEqual(["b", "c"]);
    expect(m.size).toBe(2);
  });
  it("overwriting an existing key does not grow or evict", () => {
    const m = new CappedMap<string, number>(2);
    m.set("a", 1); m.set("b", 2); m.set("a", 9);
    expect([...m.keys()]).toEqual(["a", "b"]);
    expect(m.get("a")).toBe(9);
  });
});
