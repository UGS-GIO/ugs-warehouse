import { describe, expect, it } from "vitest";
import { constantFields, previewOrder, scoreField } from "./record-fields";

// The real columns of enmin_ccus_cbgeoregion, in catalog order.
const ROW = [
  { name: "basn_nm", value: "Green River Basin" },
  { name: "id", value: 10 },
  { name: "maps", value: 0 },
  { name: "ogc_fid", value: 10 },
  { name: "ranking", value: "Limited" },
  { name: "resrvrs", value: "Frontier/Baxter Shale" },
  { name: "shap_ar", value: "" },
  { name: "_publication_date", value: 1776643200000 },
];

describe("previewOrder", () => {
  it("leads with what tells records apart, not the first columns", () => {
    const names = previewOrder(ROW, 4).map((i) => ROW[i].name);
    expect(names).toEqual(["basn_nm", "ranking", "resrvrs", "maps"]);
  });

  it("keeps column order among equals", () => {
    const flat = [{ name: "a", value: "x" }, { name: "b", value: "y" }, { name: "c", value: "z" }];
    expect(previewOrder(flat, 2)).toEqual([0, 1]);
  });
});

describe("scoreField", () => {
  it("ranks a named value above a join key and an empty cell", () => {
    expect(scoreField("basn_nm", "Green River Basin")).toBeGreaterThan(scoreField("ogc_fid", 10));
    expect(scoreField("ranking", "Limited")).toBeGreaterThan(scoreField("shap_ar", ""));
  });
});

describe("constantFields", () => {
  it("finds the columns that never vary, so they don't lead a card", () => {
    const rows = [
      [{ name: "basn_nm", value: "Green River" }, { name: "maps", value: 0 }],
      [{ name: "basn_nm", value: "Uinta" }, { name: "maps", value: 0 }],
    ];
    expect([...constantFields(rows)]).toEqual(["maps"]);
    const names = previewOrder(rows[0], 1, constantFields(rows)).map((i) => rows[0][i].name);
    expect(names).toEqual(["basn_nm"]);
  });
});
