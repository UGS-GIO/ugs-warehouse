import { describe, expect, it } from "vitest";
import { fileNameFor, fitsInQuota, formatBytes, realQuota, resumeFrom, urlFromFileName } from "./opfs";

const HREF = "https://maps-assets.geology.utah.gov/warehouse/pmtiles/hazards_qfaults_current.pmtiles";

describe("fileNameFor / urlFromFileName", () => {
  // The filename IS the manifest, so a lossy encoding would orphan every stored file.
  it("round-trips a CDN artifact URL", () => {
    expect(urlFromFileName(fileNameFor(HREF))).toBe(HREF);
  });

  it("leaves no path separator in the name", () => {
    expect(fileNameFor(HREF)).not.toContain("/");
  });

  it("round-trips a URL carrying a query and spaces", () => {
    const url = "https://example.org/a b/c.pmtiles?v=2&x=%2F";
    expect(urlFromFileName(fileNameFor(url))).toBe(url);
  });

  it("keeps two layers with the same basename apart", () => {
    const a = "https://cdn/warehouse/pmtiles/geology.pmtiles";
    const b = "https://cdn/review/pmtiles/geology.pmtiles";
    expect(fileNameFor(a)).not.toBe(fileNameFor(b));
  });
});

describe("fitsInQuota", () => {
  it("allows a download that fits under the 10% margin", () => {
    expect(fitsInQuota(100, { usage: 0, quota: 1000 })).toBe(true);
  });

  it("refuses one that would fill past the margin", () => {
    expect(fitsInQuota(950, { usage: 0, quota: 1000 })).toBe(false);
    expect(fitsInQuota(100, { usage: 950, quota: 1000 })).toBe(false);
  });

  // Browsers may report nothing; a failed write reports itself, so do not pre-emptively refuse.
  it("allows when the browser reports no quota", () => {
    expect(fitsInQuota(1e12, {})).toBe(true);
    expect(fitsInQuota(1e12, undefined)).toBe(true);
  });
});

describe("formatBytes", () => {
  it("reports decimal units, as a browser storage panel does", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(12_400_000)).toBe("12.4 MB");
    expect(formatBytes(340_000_000)).toBe("340 MB");
    expect(formatBytes(2_100_000_000)).toBe("2.1 GB");
  });
});

describe("realQuota", () => {
  it("drops Chrome's usage + 10 GiB placeholder", () => {
    expect(realQuota({ usage: 5e9, quota: 5e9 + 10 * 1024 ** 3 })).toEqual({ usage: 5e9 });
  });
  it("keeps a real quota", () => {
    expect(realQuota({ usage: 5e9, quota: 64e9 })).toEqual({ usage: 5e9, quota: 64e9 });
  });
});

describe("resumeFrom", () => {
  it("continues when the server sends the rest", () => {
    expect(resumeFrom(100, 206, "bytes 100-999/1000", "900")).toEqual({ start: 100, total: 1000 });
  });
  it("starts over when the file changed and the server sent it whole", () => {
    expect(resumeFrom(100, 200, null, "1200")).toEqual({ start: 0, total: 1200 });
  });
  it("starts over when the range doesn't begin where we stopped", () => {
    expect(resumeFrom(100, 206, "bytes 0-999/1000", "1000")).toEqual({ start: 0, total: 1000 });
  });
});
