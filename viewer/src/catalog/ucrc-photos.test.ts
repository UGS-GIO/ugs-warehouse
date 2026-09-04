import { describe, expect, it } from "vitest";
import { UCRC_CDN, encodePath, fullUrl, thumbPath, thumbUrl } from "./ucrc-photos";

const SP = "photos/05033061130000/box_1/wbd_file.jpg";

describe("UCRC photo URL helpers", () => {
  it("thumbPath inserts _thumbs/200 after the photos/ prefix", () => {
    expect(thumbPath(SP)).toBe("photos/_thumbs/200/05033061130000/box_1/wbd_file.jpg");
  });
  it("thumbPath handles a non-photos/ path", () => {
    expect(thumbPath("misc/x.jpg")).toBe("_thumbs/200/misc/x.jpg");
  });
  it("encodePath percent-encodes segments but keeps the slashes", () => {
    expect(encodePath("a b/c#d/e")).toBe("a%20b/c%23d/e");
  });
  it("fullUrl builds the CDN url for the full image", () => {
    expect(fullUrl(SP)).toBe(`${UCRC_CDN}/photos/05033061130000/box_1/wbd_file.jpg`);
  });
  it("thumbUrl builds the CDN url for the thumbnail", () => {
    expect(thumbUrl(SP)).toBe(`${UCRC_CDN}/photos/_thumbs/200/05033061130000/box_1/wbd_file.jpg`);
  });
});
