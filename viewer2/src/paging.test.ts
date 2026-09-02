import { describe, expect, it } from "vitest";

import { ALL_PAGES, pageLabel } from "./paging";

describe("pageLabel", () => {
    it("reads as a 1-based inclusive range", () => {
        expect(pageLabel(0, 7545, 50)).toBe("1–50 of 7545");
        expect(pageLabel(1, 7545, 50)).toBe("51–100 of 7545");
    });

    it("stops the last page at the row count, not the page boundary", () => {
        expect(pageLabel(150, 7545, 50)).toBe("7501–7545 of 7545");
        expect(pageLabel(4, 101, 25)).toBe("101–101 of 101");
    });

    it("says 0 of 0 when nothing matches", () => {
        expect(pageLabel(0, 0, 25)).toBe("0 of 0");
    });

    it("covers the whole list for 'All'", () => {
        expect(pageLabel(0, 431, ALL_PAGES)).toBe("1–431 of 431");
    });
});
