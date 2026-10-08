import { describe, expect, it } from "vitest";

import { parquetAsset, shownAssets, type StacDoc } from "@/stac";

const PARQUET = "application/vnd.apache.parquet";
const item = {
  assets: {
    data_flat: { href: "https://cdn.example/x/x.flat.parquet", type: PARQUET, roles: ["data"] },
    data: { href: "https://cdn.example/x/x.parquet", type: PARQUET, roles: ["data"] },
    pmtiles: { href: "https://cdn.example/x/x.pmtiles", type: "application/vnd.pmtiles", roles: ["visual"] },
  },
} as unknown as StacDoc;

describe("the flat GeoParquet copy", () => {
  it("is not listed beside the archive it duplicates", () => {
    expect(shownAssets(item.assets).map(([k]) => k)).toEqual(["data", "pmtiles"]);
  });

  it("is never the parquet the viewer reads, whatever the asset order", () => {
    expect(parquetAsset(item)?.href).toBe("https://cdn.example/x/x.parquet");
  });
});
