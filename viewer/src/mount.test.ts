import { describe, expect, it } from "vitest";
import { mountHref, toBasepath } from "./mount";

describe("toBasepath", () => {
  it("keeps a subpath mount, without its trailing slash", () => {
    expect(toBasepath("/review/viewer/")).toBe("/review/viewer");
    expect(toBasepath("/viewer/pr-219/")).toBe("/viewer/pr-219");
  });

  it("collapses the root mount to '/' rather than an empty basepath", () => {
    expect(toBasepath("/")).toBe("/");
  });
});

describe("mountHref", () => {
  it("prefixes a route with the mount, so a middle-click hits the right server path", () => {
    expect(mountHref("/discover", undefined, "/review/viewer/")).toBe("/review/viewer/discover");
    expect(mountHref("/", undefined, "/viewer/pr-219/")).toBe("/viewer/pr-219/");
  });

  it("leaves a root-mounted route alone", () => {
    expect(mountHref("/catalog", undefined, "/")).toBe("/catalog");
    expect(mountHref("/", undefined, "/")).toBe("/");
  });

  it("appends search params", () => {
    expect(mountHref("/catalog", new URLSearchParams({ c: "ugs-publications", i: "RI-232" }), "/"))
      .toBe("/catalog?c=ugs-publications&i=RI-232");
  });
});
