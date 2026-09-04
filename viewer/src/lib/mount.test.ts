import { describe, expect, it } from "vitest";
import { toBasepath } from "./mount";

describe("toBasepath", () => {
  it("keeps a subpath mount, without its trailing slash", () => {
    expect(toBasepath("/review/viewer/")).toBe("/review/viewer");
    expect(toBasepath("/viewer/pr-219/")).toBe("/viewer/pr-219");
  });

  it("collapses the root mount to '/' rather than an empty basepath", () => {
    expect(toBasepath("/")).toBe("/");
  });
});

