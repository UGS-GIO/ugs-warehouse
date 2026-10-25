import { afterEach, describe, expect, it, vi } from "vitest";
import { inFlight, limit, PROBE_MS, probe } from "./limit";

afterEach(() => { vi.useRealTimers(); });

describe("limit / probe", () => {
  it("runs four at a time", async () => {
    const gates: (() => void)[] = [];
    const runs = Array.from({ length: 6 }, () => limit(() => new Promise<void>((r) => gates.push(r))));
    await Promise.resolve();
    expect(inFlight()).toBe(4);
    gates.splice(0).forEach((g) => g());
    await vi.waitFor(() => expect(gates).toHaveLength(2));
    gates.forEach((g) => g());
    await Promise.all(runs);
    expect(inFlight()).toBe(0);
  });

  it("aborts a slow read and keeps its slot until the read stops", async () => {
    vi.useFakeTimers();
    let stop: (() => void) | undefined;
    const read = probe((signal) => new Promise<void>((_, reject) => {
      // Like fetch: rejects on abort, but a beat later.
      signal.addEventListener("abort", () => { stop = () => reject(signal.reason); });
    }));
    read.catch(() => {});
    await vi.advanceTimersByTimeAsync(PROBE_MS);
    expect(stop).toBeDefined();
    expect(inFlight()).toBe(1);   // aborted, not yet stopped: still counted
    stop?.();
    await expect(read).rejects.toThrow("timed out");
    expect(inFlight()).toBe(0);
  });
});
