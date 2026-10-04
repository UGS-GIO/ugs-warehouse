import { describe, expect, it } from "vitest";
import { isNetworkError } from "./online";

describe("isNetworkError", () => {
  // What each engine throws when fetch cannot reach the network at all.
  it("recognises a failed fetch in Chrome, Firefox and Safari", () => {
    expect(isNetworkError(new TypeError("Failed to fetch"))).toBe(true);
    expect(isNetworkError(new TypeError("NetworkError when attempting to fetch resource."))).toBe(true);
    expect(isNetworkError(new TypeError("Load failed"))).toBe(true);
  });

  // A server that answered (404, 500, bad JSON) is not "offline", and must keep its real message.
  it("does not mistake a server or parse error for being offline", () => {
    expect(isNetworkError(new Error("basemap index: 404"))).toBe(false);
    expect(isNetworkError(new SyntaxError("Unexpected token < in JSON"))).toBe(false);
    expect(isNetworkError(undefined)).toBe(false);
  });
});
