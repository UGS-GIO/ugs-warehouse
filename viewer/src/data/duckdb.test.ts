// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

// duckdb-wasm >= 1.30 defaults forceFullHTTPReads=true. We only found that after a footer query on
// a 1.35 GB topic cost 63s and a 2 GB one crashed the tab, because nothing failed — it just
// downloaded everything. These lock the opt-out in place, so a version bump or a refactor that
// drops the call fails here instead of silently costing a full download again.
const { open, instantiate, registerFileURL, connect } = vi.hoisted(() => ({
  open: vi.fn(), instantiate: vi.fn(), registerFileURL: vi.fn(),
  connect: vi.fn(async () => ({ query: vi.fn(async () => ({ toArray: () => [] })), close: vi.fn() })),
}));

vi.mock("@duckdb/duckdb-wasm", () => ({
  selectBundle: vi.fn(async (b: Record<string, unknown>) => b.eh),
  ConsoleLogger: class {},
  AsyncDuckDB: class { open = open; instantiate = instantiate; connect = connect; registerFileURL = registerFileURL; terminate = vi.fn(); },
  DuckDBDataProtocol: { HTTP: 4 },
}));
vi.stubGlobal("Worker", class { terminate() {} });

beforeEach(() => { open.mockClear(); connect.mockClear(); instantiate.mockClear(); });

const RANGE_READS = { filesystem: { forceFullHTTPReads: false, allowFullHTTPReads: false } };

describe("range reads", () => {
  it("are on for the explorer and exporter, which touch part of a file", async () => {
    const { newDuckDb } = await import("./duckdb");
    await newDuckDb();
    expect(open).toHaveBeenCalledWith(RANGE_READS);
  });

  // Also on for the whole-file readers. They fetch more bytes that way (measured: 577 MB against
  // 485 MB on the pub-search index) but finish sooner, because a query starts before the file has
  // landed rather than after: 21.7s against 34.5s for an ATTACH plus a BM25 query.
  it("are on for the review diff too", async () => {
    const { openParquet } = await import("./duckdb");
    const { close } = await openParquet({ "a.parquet": "https://cdn/a.parquet" });
    expect(open).toHaveBeenCalledWith(RANGE_READS);
    await close();
  });

  // The option cannot be changed once a connection exists, so the order matters.
  it("opens after instantiate and before anything queries", async () => {
    const { newDuckDb } = await import("./duckdb");
    await newDuckDb();
    expect(instantiate.mock.invocationCallOrder[0]).toBeLessThan(open.mock.invocationCallOrder[0]);
    expect(connect).not.toHaveBeenCalled();
  });
});
