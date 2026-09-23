import { describe, expect, it } from "vitest";
import { savingCount, subscribe, track } from "./in-flight";

describe("track", () => {
  it("counts a download until it settles", async () => {
    let done!: () => void;
    const p = track(new Promise<void>((r) => { done = r; }));
    expect(savingCount()).toBe(1);
    done();
    await p;
    expect(savingCount()).toBe(0);
  });

  // A failed download must release the guard too, or the leave-page prompt nags forever.
  it("releases on failure as well", async () => {
    await expect(track(Promise.reject(new Error("offline")))).rejects.toThrow("offline");
    expect(savingCount()).toBe(0);
  });

  it("tells subscribers when the count changes", async () => {
    const seen: number[] = [];
    const off = subscribe(() => seen.push(savingCount()));
    await track(Promise.resolve());
    off();
    expect(seen).toEqual([1, 0]);
  });
});
