import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExportFormat } from "./export-formats";

import {
  beginExport, cancelExport, consumeIfCancelled, currentExports, endRun, isCancelled,
  startRun, subscribeExport,
} from "./export-runs";

// Drain anything a previous test left live, so each case starts from an empty store.
beforeEach(() => { for (const r of [...currentExports()]) endRun(r.id); });

const run = (stem = "hazards_qfaults", fmt: ExportFormat = "shp") => {
  const id = beginExport();
  startRun({ id, stem, fmt });
  return id;
};

describe("tickets", () => {
  it("are distinct per run", () => {
    expect(beginExport()).not.toBe(beginExport());
  });

  it("only ever go up, so a cancelled id cannot be reused by a later run", () => {
    const a = beginExport();
    cancelExport(a);
    endRun(a);
    expect(beginExport()).toBeGreaterThan(a);
  });
});

describe("cancelling one run", () => {
  it("suppresses that run and no other", () => {
    const a = run("itemA"), b = run("itemB", "gpkg");
    cancelExport(a);
    expect(isCancelled(a)).toBe(true);
    expect(isCancelled(b)).toBe(false);
  });

  it("leaves the other run tracked and cancellable", () => {
    const a = run("itemA"), b = run("itemB", "gpkg");
    cancelExport(a);
    expect(currentExports().map((r) => r.id)).toEqual([b]);
    cancelExport(b);
    expect(currentExports()).toEqual([]);
  });
});

// Starting a second export while one is still running is reachable: the panel is keyed per item,
// so navigating away remounts it idle while the first run continues.
describe("overlapping runs", () => {
  it("keeps both, rather than the newer replacing the older", () => {
    const a = run("itemA"), b = run("itemB", "gpkg");
    expect(currentExports().map((r) => r.stem)).toEqual(["itemA", "itemB"]);
    expect(currentExports().map((r) => r.id)).toEqual([a, b]);
  });

  it("one finishing does not clear the other, even on the same item", () => {
    const a = run("itemA"), b = run("itemA", "gpkg");   // same stem, two formats
    endRun(a);
    expect(currentExports().map((r) => r.id)).toEqual([b]);
  });
});

// The usual moment to cancel is during the pre-flight, before the run has started.
describe("cancel before the run starts", () => {
  it("is reported once and then forgotten, so the set cannot grow unbounded", () => {
    const a = beginExport();
    cancelExport(a);
    expect(consumeIfCancelled(a)).toBe(true);
    expect(consumeIfCancelled(a)).toBe(false);
    expect(isCancelled(a)).toBe(false);
  });

  it("does not consume a ticket that was never cancelled", () => {
    expect(consumeIfCancelled(beginExport())).toBe(false);
  });

  it("endRun clears a cancelled id too", () => {
    const a = run();
    cancelExport(a);
    endRun(a);
    expect(isCancelled(a)).toBe(false);
  });
});

describe("snapshot", () => {
  it("is the same reference between changes, so useSyncExternalStore cannot loop", () => {
    run();
    const first = currentExports();
    expect(currentExports()).toBe(first);
  });

  it("is a new reference after a change", () => {
    const first = currentExports();
    run();
    expect(currentExports()).not.toBe(first);
  });
});

describe("subscribers", () => {
  it("are notified on start and on end", () => {
    const seen = vi.fn();
    const off = subscribeExport(seen);
    const a = run();
    expect(seen).toHaveBeenCalledTimes(1);
    endRun(a);
    expect(seen).toHaveBeenCalledTimes(2);
    off();
  });

  it("hear nothing once unsubscribed", () => {
    const seen = vi.fn();
    subscribeExport(seen)();
    endRun(run());
    expect(seen).not.toHaveBeenCalled();
  });

  it("are not woken by cancelling a run that never started", () => {
    const seen = vi.fn();
    const off = subscribeExport(seen);
    cancelExport(beginExport());
    expect(seen).not.toHaveBeenCalled();
    off();
  });
});
