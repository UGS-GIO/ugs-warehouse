import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ExportFormat } from "./export-formats";

import {
  beginExport, cancelExport, cancelledCount, consumeIfCancelled, currentExports, endRun,
  isCancelled, outstandingCount, startRun, subscribeExport,
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
  it("is reported to the run, which then ends it", () => {
    const a = beginExport();
    cancelExport(a);
    expect(consumeIfCancelled(a)).toBe(true);
    endRun(a);                                  // what exportItem does on a true
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

// Neither set may grow for the life of the tab: every ticket handed out is eventually released,
// and a cancel for a ticket that has already finished is ignored rather than recorded. Counts are
// compared as deltas, since other cases in this file leave tickets of their own behind.
describe("bookkeeping does not leak", () => {
  let base: { out: number; can: number };
  beforeEach(() => { base = { out: outstandingCount(), can: cancelledCount() }; });
  const delta = () => ({ out: outstandingCount() - base.out, can: cancelledCount() - base.can });

  it("releases a ticket whose pre-flight produced warnings and never ran", () => {
    const a = beginExport();
    startRun({ id: a, stem: "x", fmt: "shp" });
    endRun(a);                                  // the panel's path when warnings are returned
    expect(delta()).toEqual({ out: 0, can: 0 });
  });

  it("releases a ticket cancelled during the pre-flight", () => {
    const a = beginExport();
    cancelExport(a);
    expect(delta().can).toBe(1);
    if (consumeIfCancelled(a)) endRun(a);
    expect(delta()).toEqual({ out: 0, can: 0 });
  });

  it("ignores a cancel for a run that already finished", () => {
    const a = beginExport();
    endRun(a);
    cancelExport(a);                            // a click on a button not yet unmounted
    expect(delta()).toEqual({ out: 0, can: 0 });
    expect(isCancelled(a)).toBe(false);
  });

  it("ignores a cancel for a ticket that was never issued", () => {
    cancelExport(999_999);
    expect(delta()).toEqual({ out: 0, can: 0 });
  });

  it("stays flat across many cancelled runs", () => {
    for (let i = 0; i < 25; i++) {
      const id = beginExport();
      cancelExport(id);
      if (consumeIfCancelled(id)) endRun(id);
    }
    expect(delta()).toEqual({ out: 0, can: 0 });
  });
});
