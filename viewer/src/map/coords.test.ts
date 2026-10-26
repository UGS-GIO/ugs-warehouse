import { describe, expect, it } from "vitest";
import { loadFromUtm, parseLatLon, parseUtm, utmPoint } from "./coords";

const at = (text: string) => {
  const p = parseLatLon(text);
  return p && [Number(p.lat.toFixed(4)), Number(p.lon.toFixed(4))];
};

describe("parseLatLon", () => {
  it("reads decimal degrees in either order, with or without the west sign", () => {
    for (const t of ["40.5625, -111.6875", "40.5625 -111.6875", "-111.6875, 40.5625", "40.5625 111.6875",
      "40.5625N 111.6875W", "W111.6875 N40.5625"]) {
      expect(at(t), t).toEqual([40.5625, -111.6875]);
    }
  });

  it("reads degrees, minutes and seconds", () => {
    for (const t of [`40°33'45"N 111°41'15"W`, "40 33 45 N, 111 41 15 W", "40 33 45 -111 41 15",
      "N40° 33.75' W111° 41.25'"]) {
      expect(at(t), t).toEqual([40.5625, -111.6875]);
    }
  });

  it("refuses what is not a point in Utah", () => {
    for (const t of ["40.5", "Moab", "OFR-598", "2026 2027", "51.5 -0.12", "40 61 00 111 00 00", "T3S R2E",
      "12T 435000 4490000"]) {
      expect(parseLatLon(t), t).toBeNull();
    }
  });
});

describe("UTM zone 12N", () => {
  it("parses the common ways of writing it", () => {
    for (const t of ["12T 435000 4490000", "12 435000 4490000", "435000 4490000", "435000E 4490000N",
      "435000mE 4490000mN", "4490000N 435000E", "zone 12 435000, 4490000"]) {
      expect(parseUtm(t), t).toEqual({ e: 435000, n: 4490000 });
    }
    expect(parseUtm("40.5 -111.7")).toBeNull();
  });

  it("converts with proj4 to the point pyproj gives", async () => {
    const p = utmPoint("12T 435000 4490000", await loadFromUtm());
    expect(p?.lat).toBeCloseTo(40.558217, 5);
    expect(p?.lon).toBeCloseTo(-111.767768, 5);
    expect(utmPoint("100000 1000000", await loadFromUtm())).toBeNull();
  });
});
