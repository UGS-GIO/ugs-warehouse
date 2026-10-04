import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AreaMeta, AreaPlan } from "./area";
import type { Bbox } from "./guards";

const save = vi.fn();
const remove = vi.fn();
const discardPartial = vi.fn();
const sweepPartials = vi.fn().mockResolvedValue(0);
vi.mock("./opfs", () => ({ save, remove, discardPartial, sweepPartials }));
const saveArea = vi.fn();
const planArea = vi.fn();
vi.mock("./area", () => ({ saveArea, planArea }));

async function fresh() {
  vi.resetModules();
  return import("./queue");
}
const file = (url: string) => ({ kind: "file" as const, url, label: url });
const settle = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  saveArea.mockReset().mockResolvedValue(undefined);
  planArea.mockReset();
  save.mockReset().mockResolvedValue({});
  remove.mockReset().mockResolvedValue(undefined);
  discardPartial.mockReset().mockResolvedValue(undefined);
});

describe("download queue", () => {
  it("runs saves one at a time, in order, and drops them when done", async () => {
    const q = await fresh();
    const order: string[] = [];
    save.mockImplementation(async (url: string) => { order.push(url); });
    const done = vi.fn();
    q.onSettled(done);
    await q.enqueue([file("a"), file("b")]);
    await settle();
    await q.run();
    expect(order).toEqual(["a", "b"]);
    expect(done).toHaveBeenCalledTimes(2);
    expect(q.snapshot()).toEqual([]);
  });

  it("does not queue the same thing twice", async () => {
    const q = await fresh();
    let release!: () => void;
    save.mockImplementation(() => new Promise<void>((r) => { release = r; }));
    await q.enqueue([file("a")]);
    await q.enqueue([file("a")]);
    expect(q.snapshot()).toHaveLength(1);
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1));
    release();
  });

  it("keeps a failed save listed with its reason, and retries it", async () => {
    const q = await fresh();
    save.mockRejectedValueOnce(new Error("Download failed: 404 Not Found"));
    await q.enqueue([file("a")]);
    await q.run();
    expect(q.snapshot()[0]).toMatchObject({ state: "failed", error: "Download failed: 404 Not Found" });
    await q.retry(q.snapshot()[0].id);
    await q.run();
    expect(q.snapshot()).toEqual([]);
  });

  it("puts a save back in line when the network drops, and stops there", async () => {
    const q = await fresh();
    save.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await q.enqueue([file("a"), file("b")]);
    await q.run();
    expect(q.snapshot().map((j) => j.state)).toEqual(["queued", "queued"]);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("removes a file's replaced copies only after it succeeds", async () => {
    const q = await fresh();
    await q.enqueue([{ ...file("state"), replaces: ["quad1", "quad2"] }]);
    await q.run();
    expect(remove.mock.calls.map((c) => c[0])).toEqual(["quad1", "quad2"]);
  });

  it("discards a partial download when its job is removed", async () => {
    const q = await fresh();
    save.mockRejectedValueOnce(new Error("boom"));
    await q.enqueue([file("a")]);
    await q.run();
    await q.remove(q.snapshot()[0].id);
    expect(discardPartial).toHaveBeenCalledWith("a");
    expect(q.snapshot()).toEqual([]);
  });
});

describe("an area whose file is republished mid-save", () => {
  const box: Bbox = [0, 0, 1, 1];
  const meta: AreaMeta = { tilejson: { tiles: [], minzoom: 0, maxzoom: 0, bounds: box }, compression: 1 };
  const plan = (bytes: number): AreaPlan => ({ url: "https://cdn/x.pmtiles", tiles: [], bytes, meta, bbox: box });

  it("is cut again from the new version, once, and saved", async () => {
    const q = await fresh();
    const { FileChangedError } = await import("./opfs-name");
    saveArea.mockRejectedValueOnce(new FileChangedError("https://cdn/x.pmtiles"));
    planArea.mockResolvedValue(plan(200));
    await q.enqueue([{ kind: "area", plan: plan(100), label: "x", bytes: 100 }]);
    await q.run();
    expect(planArea).toHaveBeenCalledWith("https://cdn/x.pmtiles", [0, 0, 1, 1]);
    expect(saveArea).toHaveBeenCalledTimes(2);
    expect(saveArea.mock.calls[1][0].bytes).toBe(200);
    expect(q.snapshot()).toEqual([]);
  });

  it("fails with the reason if the file changes again", async () => {
    const q = await fresh();
    const { FileChangedError } = await import("./opfs-name");
    saveArea.mockRejectedValue(new FileChangedError("https://cdn/x.pmtiles"));
    planArea.mockResolvedValue(plan(200));
    await q.enqueue([{ kind: "area", plan: plan(100), label: "x", bytes: 100 }]);
    await q.run();
    expect(q.snapshot()[0]).toMatchObject({ state: "failed", error: "x.pmtiles was republished during the save." });
  });
});

describe("a save queued as the queue finishes", () => {
  it("still runs, rather than waiting for the next trigger", async () => {
    const q = await fresh();
    let late: Promise<void> | null = null;
    // Enqueue the second job after the run's last look at the queue, before the run has ended:
    // the moment the lock is released.
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async (name: string, fn: () => Promise<void>) => {
          await fn();
          if (name !== "ugs-offline-queue") return;   // the runner's lock, not the queue file's
          late ??= q.enqueue([file("b")]);
          await late;
        },
      },
    });
    try {
      await q.enqueue([file("a")]);
      await q.run();
      await vi.waitFor(() => expect(save.mock.calls.map((c) => c[0])).toEqual(["a", "b"]));
      expect(q.snapshot()).toEqual([]);
    } finally {
      Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
    }
  });
});

describe("two tabs", () => {
  it("each adds to the queue on disk, so neither drops the other's save", async () => {
    // One queue.json both tabs read and write, as OPFS is shared across an origin's tabs.
    let disk: string | null = null;
    const handle = {
      getFile: async () => { if (disk === null) throw new DOMException("missing", "NotFoundError"); return new File([disk], "q"); },
      createWritable: async () => ({ write: async (t: string) => { disk = t; }, close: async () => {} }),
    };
    Object.defineProperty(navigator, "storage", {
      configurable: true, value: { getDirectory: async () => ({ getFileHandle: async () => handle }) },
    });
    const { onlineManager } = await import("@tanstack/react-query");
    onlineManager.setOnline(false);   // queued, not run
    try {
      const a = await fresh();
      const b = await fresh();
      await a.enqueue([file("a")]);
      await b.enqueue([file("b")]);
      await a.enqueue([file("c")]);   // tab A has not seen B's save; it must not write over it
      expect(JSON.parse(disk ?? "[]").map((j: { url: string }) => j.url)).toEqual(["a", "b", "c"]);
    } finally {
      onlineManager.setOnline(true);
      Object.defineProperty(navigator, "storage", { configurable: true, value: undefined });
    }
  });
});
