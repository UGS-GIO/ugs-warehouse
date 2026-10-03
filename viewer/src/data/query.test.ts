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
});
