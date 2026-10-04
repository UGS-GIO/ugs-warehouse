import { beforeEach, describe, expect, it, vi } from "vitest";

const files = vi.fn();
const remove = vi.fn();
vi.mock("./opfs", () => ({
  list: files, remove, listPartials: vi.fn().mockResolvedValue([]), quota: vi.fn().mockResolvedValue({}),
}));
vi.mock("./area", () => ({ loadStoredAreas: vi.fn().mockResolvedValue([]), removeArea: vi.fn() }));
const blocks = vi.fn();
const removeCogArea = vi.fn();
vi.mock("./block-store", () => ({ listCogAreas: blocks, removeCogArea }));
vi.mock("./engine", () => ({ engineBytes: vi.fn().mockResolvedValue(0), removeEngine: vi.fn() }));
const setStoredBasemaps = vi.fn();
vi.mock("./basemap", () => ({ setStoredBasemaps }));
let settle: () => void = () => {};
vi.mock("./queue", () => ({ onSettled: (fn: () => void) => { settle = fn; }, remove: vi.fn() }));

async function fresh() {
  vi.resetModules();
  return import("./store");
}
const file = (url: string) => ({ url, bytes: 10, savedAt: 0 });
const tick = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  files.mockReset().mockResolvedValue([file("a.pmtiles")]);
  blocks.mockReset().mockResolvedValue([]);
  remove.mockReset().mockResolvedValue(undefined);
  removeCogArea.mockReset().mockResolvedValue(undefined);
  setStoredBasemaps.mockReset();
});

describe("offline store", () => {
  it("reads storage when first subscribed, and not before", async () => {
    const s = await fresh();
    expect(files).not.toHaveBeenCalled();
    expect(s.snapshot().ready).toBe(false);
    const seen = vi.fn();
    s.subscribe(seen);
    await s.whenReady();
    expect(s.snapshot()).toMatchObject({ ready: true, files: [file("a.pmtiles")] });
    expect(seen).toHaveBeenCalled();
    expect(setStoredBasemaps).toHaveBeenCalledWith(["a.pmtiles"]);
  });

  it("reads once for changes that arrive during a read, then once more", async () => {
    const s = await fresh();
    await Promise.all([s.refresh(), s.refresh(), s.refresh()]);
    expect(files).toHaveBeenCalledTimes(2);
  });

  it("re-reads after a delete, so every control sees it gone", async () => {
    const s = await fresh();
    await s.refresh();
    files.mockResolvedValue([]);
    await s.removeFiles(["a.pmtiles"]);
    expect(remove).toHaveBeenCalledWith("a.pmtiles");
    expect(s.snapshot().files).toEqual([]);
  });

  it("deletes a block-stored area through the block store", async () => {
    const s = await fresh();
    await s.removeAreas([{ url: "t.parquet", kind: "blocks" }]);
    expect(removeCogArea).toHaveBeenCalledWith("t.parquet");
  });

  it("re-reads when a download settles", async () => {
    const s = await fresh();
    await s.refresh();
    files.mockResolvedValue([file("a.pmtiles"), file("b.pmtiles")]);
    settle();
    await tick();
    await s.whenReady();
    await vi.waitFor(() => expect(s.snapshot().files).toHaveLength(2));
  });

  it("marks itself read even when storage cannot be read", async () => {
    const s = await fresh();
    files.mockRejectedValue(new Error("blocked"));
    await s.refresh();
    expect(s.snapshot()).toMatchObject({ ready: true, files: [] });
  });
});
