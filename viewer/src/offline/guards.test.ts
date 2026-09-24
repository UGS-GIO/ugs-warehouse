import { describe, expect, it } from "vitest";
import { sameVersion } from "./guards";

// Just enough of a folder for readJson: named files with text.
const folder = (files: Record<string, string>) => ({
  getFileHandle: async (name: string) => {
    const text = files[name];
    if (text === undefined) throw new DOMException("missing", "NotFoundError");
    return { getFile: async () => new File([text], name) };
  },
});

describe("sameVersion", () => {
  it("catches an unfinished save of another version, which has no meta yet", async () => {
    expect(await sameVersion(folder({ "version.json": '{"version":"v1"}' }), "v2", null)).toBe(false);
  });
  it("keeps an unfinished save of the same version, so a resume reuses its parts", async () => {
    expect(await sameVersion(folder({ "version.json": '{"version":"v2"}' }), "v2", null)).toBe(true);
  });
  it("falls back to the meta's version for a folder saved before the stamp", async () => {
    expect(await sameVersion(folder({}), "v2", "v2")).toBe(true);
    expect(await sameVersion(folder({}), "v2", "v1")).toBe(false);
  });
  it("drops a folder with neither: nothing says which version its parts came from", async () => {
    expect(await sameVersion(folder({}), "v2", null)).toBe(false);
  });
});
