import { describe, expect, it } from "vitest";
import { hideParam, parseHideParam } from "./layer-visibility";

describe("hide param", () => {
  it("round-trips ids and drops empty", () => {
    expect([...parseHideParam("a,b")]).toEqual(["a", "b"]);
    expect(parseHideParam(undefined).size).toBe(0);
    expect(hideParam(new Set())).toBeUndefined();
  });
  it("keeps only layers still on the map", () => {
    expect(hideParam(new Set(["a", "b"]), ["b", "c"])).toBe("b");
    expect(hideParam(new Set(["a"]), [])).toBeUndefined();
  });
});
