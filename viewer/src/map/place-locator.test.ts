import { afterEach, describe, expect, it, vi } from "vitest";
import { locate } from "./place-locator";

const reply = (body: unknown) => vi.fn(async () => new Response(JSON.stringify(body)));

afterEach(() => vi.unstubAllGlobals());

describe("locate", () => {
  it("returns the candidate extent as [w, s, e, n]", async () => {
    vi.stubGlobal("fetch", reply({ candidates: [{ location: { x: -110.7, y: 37.94 },
      extent: { xmin: -110.71, ymin: 37.93, xmax: -110.69, ymax: 37.95 } }] }));
    await expect(locate({ text: "Henry Mountains", magicKey: "k" })).resolves.toEqual([-110.71, 37.93, -110.69, 37.95]);
  });

  it("falls back to the point when a candidate has no extent", async () => {
    vi.stubGlobal("fetch", reply({ candidates: [{ location: { x: -109.55, y: 38.57 } }] }));
    await expect(locate({ text: "Moab", magicKey: "k" })).resolves.toEqual([-109.55, 38.57, -109.55, 38.57]);
  });

  it("rejects with not found when there are no candidates", async () => {
    vi.stubGlobal("fetch", reply({ candidates: [] }));
    await expect(locate({ text: "x", magicKey: "k" })).rejects.toThrow("not found");
  });
});

describe("service errors", () => {
  it("rejects an ArcGIS error sent with a 200", async () => {
    vi.stubGlobal("fetch", reply({ error: { code: 500, message: "Unable to complete operation." } }));
    await expect(locate({ text: "x", magicKey: "k" })).rejects.toThrow("Unable to complete operation.");
  });
});
