import { describe, expect, it } from "vitest";

import { exportFormatsFor, FORMATS } from "./export-formats";

describe("exportFormatsFor", () => {
  it("offers every format for spatial data", () => {
    expect(exportFormatsFor(true).map((f) => f.id)).toEqual(FORMATS.map((f) => f.id));
    expect(exportFormatsFor(true).find((f) => f.id === "csv")?.label).toBe("CSV (WKT)");
  });
  it("offers a plain table CSV alone, with no WKT", () => {
    expect(exportFormatsFor(false)).toEqual([{ id: "csv", label: "CSV", hint: ".csv" }]);
  });
});
