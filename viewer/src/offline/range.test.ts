import { describe, expect, it } from "vitest";
import { rangeHeaders, rangeStatus, resolveRange, STORABLE } from "./range";

const SIZE = 1000;

describe("resolveRange", () => {
  it("returns the full body when no Range was asked for", () => {
    expect(resolveRange(null, SIZE)).toEqual({ kind: "full", size: SIZE });
  });

  it("resolves a closed range", () => {
    expect(resolveRange("bytes=0-99", SIZE)).toEqual({ kind: "partial", start: 0, end: 99, size: SIZE });
  });

  it("resolves an open-ended range to the last byte", () => {
    expect(resolveRange("bytes=900-", SIZE)).toEqual({ kind: "partial", start: 900, end: 999, size: SIZE });
  });

  // How duckdb-wasm reads a parquet footer, and pmtiles the header of a short archive.
  it("resolves a suffix range", () => {
    expect(resolveRange("bytes=-100", SIZE)).toEqual({ kind: "partial", start: 900, end: 999, size: SIZE });
  });

  it("clamps a suffix longer than the file to the whole file", () => {
    expect(resolveRange("bytes=-5000", SIZE)).toEqual({ kind: "partial", start: 0, end: 999, size: SIZE });
  });

  it("clamps an end past the last byte", () => {
    expect(resolveRange("bytes=990-5000", SIZE)).toEqual({ kind: "partial", start: 990, end: 999, size: SIZE });
  });

  // pmtiles reads the archive size out of the 416's Content-Range and retries, so this path matters.
  it("reports a start at or past the end as unsatisfiable", () => {
    expect(resolveRange("bytes=1000-1099", SIZE).kind).toBe("unsatisfiable");
    expect(resolveRange("bytes=-0", SIZE).kind).toBe("unsatisfiable");
  });

  it("falls back to the full body on a form it does not handle", () => {
    expect(resolveRange("bytes=0-10,20-30", SIZE).kind).toBe("full");   // multipart
    expect(resolveRange("items=0-10", SIZE).kind).toBe("full");          // not bytes
    expect(resolveRange("garbage", SIZE).kind).toBe("full");
  });
});

describe("rangeHeaders / rangeStatus", () => {
  it("describes a partial reply the way a client expects", () => {
    const r = resolveRange("bytes=0-99", SIZE);
    const h = rangeHeaders(r, "application/octet-stream");
    expect(rangeStatus(r)).toBe(206);
    expect(h.get("Content-Range")).toBe("bytes 0-99/1000");
    expect(h.get("Content-Length")).toBe("100");
    expect(h.get("Accept-Ranges")).toBe("bytes");
  });

  // pmtiles' FetchSource throws when a 200 carries more bytes than it asked for, so a full reply
  // must declare the real size and advertise that ranges are available.
  it("declares the real size on a full reply", () => {
    const h = rangeHeaders(resolveRange(null, SIZE), "application/octet-stream");
    expect(h.get("Content-Length")).toBe("1000");
    expect(h.get("Accept-Ranges")).toBe("bytes");
    expect(h.get("Content-Range")).toBeNull();
  });

  it("reports the total on a 416, which is what clients retry from", () => {
    const r = resolveRange("bytes=2000-", SIZE);
    expect(rangeStatus(r)).toBe(416);
    expect(rangeHeaders(r, "application/octet-stream").get("Content-Range")).toBe("bytes */1000");
  });
});

describe("STORABLE", () => {
  it("matches the artifacts the store serves", () => {
    expect(STORABLE.test("/warehouse/pmtiles/a.pmtiles")).toBe(true);
    expect(STORABLE.test("/pubs/cog/plate.tif")).toBe(true);
    expect(STORABLE.test("/warehouse/archive/a.parquet")).toBe(true);
  });

  it("leaves everything else on the network", () => {
    expect(STORABLE.test("/warehouse/stac/catalog.json")).toBe(false);
    expect(STORABLE.test("/assets/index-abc123.js")).toBe(false);
  });
});
