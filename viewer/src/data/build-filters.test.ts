// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { buildFilters } from "./data-explorer";
import type { ColType } from "./download";

const TYPES: Record<string, ColType> = { depth: "number", name: "text" };

describe("buildFilters", () => {
  it("drops blank inputs so an emptied box is not a filter", () => {
    expect(buildFilters({ name: { text: "  " }, depth: { min: "", max: "" } }, TYPES)).toEqual([]);
  });

  it("reads a numeric column as a range and a text one as a substring", () => {
    expect(buildFilters({ depth: { min: "10", max: "20" }, name: { text: "well" } }, TYPES))
      .toEqual([
        { col: "depth", kind: "number", min: 10, max: 20 },
        { col: "name", kind: "text", contains: "well" },
      ]);
  });

  it("keeps a half-open range", () => {
    expect(buildFilters({ depth: { min: "10" } }, TYPES))
      .toEqual([{ col: "depth", kind: "number", min: 10, max: undefined }]);
  });

  it("ignores a numeric box holding something unparseable", () => {
    expect(buildFilters({ depth: { min: "abc" } }, TYPES)).toEqual([]);
  });

  // Types arrive from their own query, so the first keystroke can land before them. Text is the
  // safe reading: a substring match on a numeric column returns fewer rows, never wrong ones.
  it("falls back to text when the column type is not known yet", () => {
    expect(buildFilters({ depth: { text: "12" } }, undefined))
      .toEqual([{ col: "depth", kind: "text", contains: "12" }]);
  });
});
