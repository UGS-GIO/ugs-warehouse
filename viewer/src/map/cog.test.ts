import { beforeEach, describe, expect, it, vi } from "vitest";
import { parseCogTileUrl, renderOptsForBands } from "./cog";

const HREF = "https://maps-assets.example/cogs/park_city.tif";

describe("parseCogTileUrl", () => {
  it("parses a plain cog:// tile URL, keeping the https:// slashes in the href", () => {
    expect(parseCogTileUrl(`cog://${HREF}/5/10/20`)).toEqual({ href: HREF, z: 5, x: 10, y: 20 });
  });

  it("tolerates trailing query params (cache-busting / auth) instead of returning blank tiles", () => {
    expect(parseCogTileUrl(`cog://${HREF}/5/10/20?fresh=1&t=abc`)).toEqual({
      href: HREF,
      z: 5,
      x: 10,
      y: 20,
    });
  });

  it("returns null for a non-cog URL", () => {
    expect(parseCogTileUrl(`https://${HREF}/5/10/20`)).toBeNull();
    expect(parseCogTileUrl("cog://only/two/segments")).toBeNull();
  });
});

describe("renderOptsForBands", () => {
  it("passes RGB(A) plates (>= 3 bands) straight through with an identity rescale", () => {
    const rgb = { bidx: [1, 2, 3], rescale: [[0, 255], [0, 255], [0, 255]] };
    expect(renderOptsForBands(3)).toEqual(rgb); // RGB
    expect(renderOptsForBands(4)).toEqual(rgb); // RGBA
  });

  it("does NOT force a 3-band read on single-/two-band COGs (would be out of bounds)", () => {
    // Empty opts => tiler applies its own default (band 1 palette/colormap), no throw.
    expect(renderOptsForBands(1)).toEqual({});
    expect(renderOptsForBands(2)).toEqual({});
    expect(renderOptsForBands(0)).toEqual({});
  });
});

describe("ensureCogProtocol", () => {
  // The module keeps `cogReady`/`ct` as singletons, so reset the registry and re-import per test.
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  it("resets after a transient init failure so a later call retries, registering the protocol once", async () => {
    const addProtocol = vi.fn();
    vi.doMock("maplibre-gl", () => ({ default: { addProtocol } }));
    const init = vi
      .fn()
      .mockRejectedValueOnce(new Error("wasm blip")) // first init fails (transient)
      .mockResolvedValue(undefined); // retry succeeds
    vi.doMock("cog-tiler-wasm", () => ({ init, openCog: vi.fn() }));

    const { ensureCogProtocol } = await import("./cog");

    await expect(ensureCogProtocol()).rejects.toThrow("wasm blip");
    await expect(ensureCogProtocol()).resolves.toBeUndefined();

    expect(init).toHaveBeenCalledTimes(2); // the reset let the second call retry
    expect(addProtocol).toHaveBeenCalledTimes(1); // only the successful attempt reaches addProtocol
  });
});
