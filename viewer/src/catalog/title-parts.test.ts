import { describe, expect, it } from "vitest";

import { isInterim, titleParts } from "./title-parts";

describe("titleParts", () => {
  it("leads with the quadrangle that tells a series apart", () => {
    expect(titleParts("Photogeologic map of the Moab-12 quadrangle, Grand County, Utah")).toEqual(
      { lead: "Moab-12 quadrangle", kind: "Photogeologic map", where: "Grand County" });
    expect(titleParts("Geologic map and coal resources of the Emery West quadrangle, Emery and Sevier Counties, Utah"))
      .toEqual({ lead: "Emery West quadrangle", kind: "Geologic map and coal resources", where: "Emery and Sevier Counties" });
  });

  it("keeps a plural quadrangle name and a non-Utah county list", () => {
    expect(titleParts("Geologic map of the Moab and eastern part of the San Rafael Desert 30' x 60' quadrangles, Grand and Emery Counties, Utah, and Mesa County, Colorado"))
      .toEqual({
        lead: "Moab and eastern part of the San Rafael Desert 30' x 60' quadrangles", kind: "Geologic map",
        where: "Grand and Emery Counties, Utah, and Mesa County, Colorado",
      });
    expect(titleParts("Geology, structure, and uranium deposits of the Moab [1 x 2] quadrangle, Colorado and Utah")?.where)
      .toBe("Colorado and Utah");
  });

  it("drops a bare Utah, which every title has", () => {
    expect(titleParts("Landslide map of the Moab 30' x 60' quadrangle, Utah")?.where).toBe("");
  });

  it("splits a named place with its county", () => {
    expect(titleParts("Geologic hazards of Moab-Spanish Valley, Grand County, Utah")).toEqual(
      { lead: "Moab-Spanish Valley", kind: "Geologic hazards", where: "Grand County" });
    expect(titleParts("The hydrogeology of Moab-Spanish Valley, Grand and San Juan Counties, Utah, with emphasis on maps for water-resource management")?.where)
      .toBe("Grand and San Juan Counties");
  });

  it("moves an edition year into the lead", () => {
    expect(titleParts("Utah Mining 2018: Metals, Industrial Minerals, Coal, Uranium, and Unconventional Fuels")?.lead)
      .toBe("Utah Mining 2018");
    expect(titleParts("Utah Mining - 2023 Metals, Industrial Minerals, Uranium, Coal, and Unconventional Fuels")).toEqual(
      { lead: "Utah Mining 2023", kind: "Metals, Industrial Minerals, Uranium, Coal, and Unconventional Fuels", where: "" });
  });

  it("returns null rather than guess", () => {
    expect(titleParts("Petroleum resources of the Paradox Basin")).toBeNull();
    expect(titleParts("Preliminary geologic reconnaissance for sanitary landfill sites for City of Moab, Grand County, Utah"))
      .toBeNull();
  });
});

describe("isInterim", () => {
  it("reads the title's leading word", () => {
    expect(isInterim("Interim geologic map of the Moab quadrangle, Grand County, Utah")).toBe(true);
    expect(isInterim("Geologic map of the Moab 7.5' quadrangle")).toBe(false);
  });
});
