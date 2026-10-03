import { describe, expect, it } from "vitest";
import type { StacDoc } from "@/stac";
import { buildIndex, toSearchDoc } from "./search-index";

// Publications shaped like the catalog's index records. The neighbours share the series and most of
// the number, and OFR-290's title is full of m-words, so a series-ID query has to rank, not just match.
const pub = (sid: string, title: string, props: Record<string, unknown> = {}): [string, StacDoc] => [
  `ugs-publications/${sid.split("-")[0]}`,
  { id: sid, properties: { title, "ugs:series_id": sid, ...props } } as unknown as StacDoc,
];

const CATALOG = [
  pub("OFR-770", "Geologic map of the Logan quadrangle", { "ugs:pub_type": "Open File Report" }),
  pub("OFR-771", "Geologic map of the Smithfield quadrangle", { "ugs:pub_type": "Open File Report" }),
  pub("OFR-772", "Geologic map of the Richmond quadrangle", { "ugs:pub_type": "Open File Report" }),
  pub("OFR-290", "Metal mines and mills of the Tintic district",
    { "ugs:pub_type": "Open File Report", "ugs:topic": "mineral-energy" }),
  pub("M-290", "Geologic map of the Beaver quadrangle", { "ugs:pub_type": "Map" }),
  pub("MP-173", "Selected papers on the Uinta Basin", { "ugs:pub_type": "Miscellaneous Publication" }),
  pub("CR-91-14DF", "Engineering geology of the Jordan Narrows", { "ugs:pub_type": "Contract Report" }),
  // Different files whose ids only differ by a hyphen (both live in the Mining District series).
  pub("MD-86-7", "Report on the Horn Silver mine", { "ugs:pub_type": "Mining District Files" }),
  pub("MD-867", "Report on the Cactus mine", { "ugs:pub_type": "Mining District Files" }),
];

const catalogIndex = () => buildIndex([], CATALOG.map(([coll, doc]) => toSearchDoc(coll, doc))).index;
const search = (q: string): string[] => catalogIndex().search(q).map((h) => String(h.id));

describe("catalog search by series ID", () => {
  it.each([
    ["OFR-771", "ugs-publications/OFR/OFR-771"],
    ["ofr-771", "ugs-publications/OFR/OFR-771"],
    ["OFR 771", "ugs-publications/OFR/OFR-771"],
    ["OFR771", "ugs-publications/OFR/OFR-771"],
    ["M-290", "ugs-publications/M/M-290"],
    ["m-290", "ugs-publications/M/M-290"],
    ["M 290", "ugs-publications/M/M-290"],
    ["CR-91-14DF", "ugs-publications/CR/CR-91-14DF"],
    ["MD-86-7", "ugs-publications/MD/MD-86-7"],
    ["MD-867", "ugs-publications/MD/MD-867"],
    ["md-867", "ugs-publications/MD/MD-867"],
  ])("%s ranks that publication first", (q, want) => {
    expect(search(q)[0]).toBe(want);
  });

  it("does not guess between ids that differ only by hyphens", () => {
    // Only the hyphenless key (MD867) matches, and both files have it.
    expect(search("MD8-67")).toEqual([]);
  });

  it("leaves a caller's filter in charge of what comes back", () => {
    const hits = catalogIndex().search("M-290", { filter: (r) => r.id !== "ugs-publications/M/M-290" });
    expect(hits.map((h) => h.id)).not.toContain("ugs-publications/M/M-290");
  });

  it("finds every publication that starts with a partial series ID", () => {
    expect(new Set(search("OFR-77"))).toEqual(new Set([
      "ugs-publications/OFR/OFR-770", "ugs-publications/OFR/OFR-771", "ugs-publications/OFR/OFR-772",
    ]));
  });

  it("does not let an ordinary word match a bare series code", () => {
    expect(search("map")).not.toContain("ugs-publications/MP/MP-173");
  });
});
