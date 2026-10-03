import { describe, expect, it } from "vitest";
import type { StacDoc } from "@/stac";
import { buildIndex, toSearchDoc } from "./search-index";

// Publications shaped like the catalog's index records, with live IDs: OFR-771 and M-290 only exist
// as OFR-771DM and M-290DR, and their neighbours share the series and most of the number. MD-1320's
// series text ("OFR 78-791") shares "ofr" and a near number with OFR-771, and OFR-290's title is full
// of m-words, so a series ID has to be looked up, not just ranked.
const pub = (sid: string, title: string, props: Record<string, unknown> = {}): [string, StacDoc] => [
  `ugs-publications/${sid.split("-")[0]}`,
  { id: sid, properties: { title, "ugs:series_id": sid, ...props } } as unknown as StacDoc,
];

const CATALOG = [
  pub("OFR-770DM", "Interim Geologic Map of the Logan Quadrangle", { "ugs:pub_type": "Open File Report" }),
  pub("OFR-771DM", "Interim Geologic Map of the Blanding North Quadrangle, San Juan County, Utah",
    { "ugs:pub_type": "Open File Report" }),
  pub("OFR-772DM", "Interim Geologic Map of the Richmond Quadrangle", { "ugs:pub_type": "Open File Report" }),
  pub("OFR-773", "Interim Geologic Map of the Wildcat Mountain and East Part of the Currie Quadrangles",
    { "ugs:pub_type": "Open File Report" }),
  pub("MD-1320", "Unprospected Zone of Pyritic Alteration in West-Central Utah",
    { "ugs:pub_type": "Mining District Files", "ugs:series": "OFR 78-791", "ugs:topic": "mineral-energy" }),
  pub("OFR-290", "Metal mines and mills of the Tintic district",
    { "ugs:pub_type": "Open File Report", "ugs:topic": "mineral-energy" }),
  pub("M-290DR", "Geologic Map of the Tickville Spring Quadrangle, Salt Lake and Utah Counties, Utah",
    { "ugs:pub_type": "Map" }),
  pub("M-291DR", "Geologic Map of the Jordan Narrows Quadrangle", { "ugs:pub_type": "Map" }),
  pub("MP-173", "Selected papers on the Uinta Basin", { "ugs:pub_type": "Miscellaneous Publication" }),
  pub("CR-91-14DF", "Engineering geology of the Jordan Narrows", { "ugs:pub_type": "Contract Report" }),
  // Different files whose ids only differ by a hyphen (both live in the Mining District series).
  pub("MD-86-7", "Report on the Horn Silver mine", { "ugs:pub_type": "Mining District Files" }),
  pub("MD-867", "Report on the Cactus mine", { "ugs:pub_type": "Mining District Files" }),
  // One map listed twice under ids that differ only by case, as in the live catalog, and a title
  // that cites it, which outranks both on words alone.
  pub("OFR-673DM", "Geologic Map of the Kanab Quadrangle", { "ugs:pub_type": "Open File Report" }),
  pub("OFR-673dm", "Geologic Map of the Kanab Quadrangle", { "ugs:pub_type": "Open File Report" }),
  pub("OFR-799", "Supplement to OFR 673", { "ugs:pub_type": "Open File Report" }),
];

const catalogIndex = () => buildIndex([], CATALOG.map(([coll, doc]) => toSearchDoc(coll, doc))).index;
const search = (q: string): string[] => catalogIndex().search(q).map((h) => String(h.id));

describe("catalog search by series ID", () => {
  it.each([
    ["OFR-771", "ugs-publications/OFR/OFR-771DM"],
    ["ofr-771", "ugs-publications/OFR/OFR-771DM"],
    ["OFR 771", "ugs-publications/OFR/OFR-771DM"],
    ["OFR771", "ugs-publications/OFR/OFR-771DM"],
    ["OFR-771DM", "ugs-publications/OFR/OFR-771DM"],
    ["OFR-773", "ugs-publications/OFR/OFR-773"],
    ["M-290", "ugs-publications/M/M-290DR"],
    ["m-290", "ugs-publications/M/M-290DR"],
    ["M 290", "ugs-publications/M/M-290DR"],
    ["CR-91-14DF", "ugs-publications/CR/CR-91-14DF"],
    ["MD-86-7", "ugs-publications/MD/MD-86-7"],
    ["MD-867", "ugs-publications/MD/MD-867"],
    ["md-867", "ugs-publications/MD/MD-867"],
  ])("%s ranks that publication first", (q, want) => {
    expect(search(q)[0]).toBe(want);
  });

  it("does not fuzzy-match a series number to its neighbours", () => {
    // 773 and 791 are one digit off 771: close for a typo allowance, but a different publication.
    expect(search("OFR-771")).toEqual(["ugs-publications/OFR/OFR-771DM"]);
  });

  it("treats ids that differ only by case as one publication", () => {
    expect(search("OFR-673")[0]).toBe("ugs-publications/OFR/OFR-673DM");
    expect(search("OFR-673dm")[0]).toBe("ugs-publications/OFR/OFR-673DM");
  });

  it("does not guess between ids that differ only by hyphens", () => {
    // Only the hyphenless key (MD867) matches, and both files have it.
    expect(search("MD8-67")).toEqual([]);
  });

  it("leaves a caller's filter in charge of what comes back", () => {
    const hits = catalogIndex().search("M-290", { filter: (r) => r.id !== "ugs-publications/M/M-290DR" });
    expect(hits.map((h) => h.id)).not.toContain("ugs-publications/M/M-290DR");
  });

  it("finds every publication that starts with a partial series ID", () => {
    expect(new Set(search("OFR-77"))).toEqual(new Set([
      "ugs-publications/OFR/OFR-770DM", "ugs-publications/OFR/OFR-771DM",
      "ugs-publications/OFR/OFR-772DM", "ugs-publications/OFR/OFR-773",
    ]));
  });

  it("does not let an ordinary word match a bare series code", () => {
    expect(search("map")).not.toContain("ugs-publications/MP/MP-173");
  });
});
