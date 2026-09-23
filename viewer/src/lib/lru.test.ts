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

describe("CappedMap pinning", () => {
  // An archive served from a downloaded file has no network copy behind it, so evicting it would
  // turn an offline layer into a failed fetch.
  it("evicts unpinned entries and keeps pinned ones", () => {
    const pinned = new Set(["keep"]);
    const m = new CappedMap<string, number>(2, (k) => pinned.has(k));
    m.set("keep", 1);
    m.set("a", 2);
    m.set("b", 3);
    m.set("c", 4);
    expect(m.has("keep")).toBe(true);
    expect(m.size).toBeLessThanOrEqual(3);
    expect(m.has("c")).toBe(true);
  });

  it("never evicts the entry just inserted", () => {
    const m = new CappedMap<string, number>(1);
    m.set("a", 1);
    m.set("b", 2);
    expect([...m.keys()]).toEqual(["b"]);
  });

  it("grows past the cap when everything is pinned, rather than dropping a live archive", () => {
    const m = new CappedMap<string, number>(1, () => true);
    m.set("a", 1);
    m.set("b", 2);
    expect(m.size).toBe(2);
  });
});
