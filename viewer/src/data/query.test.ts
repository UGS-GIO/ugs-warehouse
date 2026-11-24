import { describe, expect, it } from "vitest";
import { matchesQuery, parseQuery } from "./query";

const ofr771 = { title: "Geologic map of the Smithfield quadrangle", text: "Open File Report", itemId: "OFR-771" };

describe("matchesQuery and a series ID", () => {
  it("keeps an item whose ID is the quoted phrase", () => {
    expect(matchesQuery(parseQuery('"OFR-771"'), ofr771)).toBe(true);
  });
  it("drops an item whose ID is excluded", () => {
    expect(matchesQuery(parseQuery("map -OFR-771"), ofr771)).toBe(false);
  });
  it("matches has:units and -has:units against capability tags", () => {
    const withUnits = { ...ofr771, has: "units" };
    expect(matchesQuery(parseQuery("has:units"), withUnits)).toBe(true);
    expect(matchesQuery(parseQuery("has:units"), ofr771)).toBe(false);
    expect(matchesQuery(parseQuery("-has:units"), ofr771)).toBe(true);
    expect(matchesQuery(parseQuery("-has:units"), withUnits)).toBe(false);
  });
});
