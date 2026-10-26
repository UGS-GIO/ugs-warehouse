import { describe, expect, it, vi } from "vitest";
import { findPlss, parsePlss } from "./plss";

const readWhere = vi.hoisted(() => vi.fn());
vi.mock("@/data/parquet-lite", () => ({ readWhere }));

describe("parsePlss", () => {
  it("reads a township, with or without a section, in the usual spellings", () => {
    expect(parsePlss("T3S R2E")).toEqual({ township: "T3S R2E", section: undefined });
    for (const t of ["T3S R2E sec 12", "t3s r2e S12", "T03S R02E Section 12", "Sec. 12, T3S, R2E", "T.3S. R.2E. sec. 12"]) {
      expect(parsePlss(t), t).toEqual({ township: "T3S R2E", section: 12 });
    }
  });

  it("refuses what is not a township", () => {
    for (const t of ["T3S", "R2E", "T3S R2E sec 37", "T3S R2E lot 4", "Moab", "40.5 -111.7"]) {
      expect(parsePlss(t), t).toBeNull();
    }
  });
});

describe("findPlss", () => {
  const box = (w: number, s: number, e: number, n: number) => ({ bbox_xmin: w, bbox_ymin: s, bbox_xmax: e, bbox_ymax: n });

  it("outlines a section in each meridian that has one, merging its split polygons", async () => {
    readWhere.mockResolvedValueOnce([
      { label: "T3S R2E", snum: 12, basemeridian: "26", ...box(-111.8, 40.5, -111.79, 40.51) },
      { label: "T3S R2E", snum: 12, basemeridian: "26", ...box(-111.8, 40.51, -111.79, 40.52) },
      { label: "T3S R2E", snum: 12, basemeridian: "30", ...box(-109.9, 40.1, -109.89, 40.11) },
    ]);
    const hits = await findPlss({ township: "T3S R2E", section: 12 }, { sections: "s.parquet" });
    expect(hits).toEqual([
      { label: "T3S R2E Sec. 12", meridian: "Salt Lake Meridian", bbox: [-111.8, 40.5, -111.79, 40.52] },
      { label: "T3S R2E Sec. 12", meridian: "Uintah Meridian", bbox: [-109.9, 40.1, -109.89, 40.11] },
    ]);
    const [url, keys, where] = readWhere.mock.calls[0];
    expect([url, keys]).toEqual(["s.parquet", ["label", "snum", "basemeridian"]]);
    expect(where({ label: "T3S R2E", snum: 12 })).toBe(true);
    expect(where({ label: "T3S R2E", snum: 13 })).toBe(false);
  });

  it("reads the townships file for a township, and nothing without one", async () => {
    readWhere.mockResolvedValueOnce([]);
    expect(await findPlss({ township: "T3S R2E" }, { townships: "t.parquet" })).toEqual([]);
    expect(readWhere.mock.calls.at(-1)?.[0]).toBe("t.parquet");
    expect(await findPlss({ township: "T3S R2E", section: 1 }, { townships: "t.parquet" })).toEqual([]);
  });
});
