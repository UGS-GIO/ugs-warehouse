import { describe, expect, it } from "vitest";
import type { StacDoc } from "@/stac";
import { buildIndex, catalogIndex as sharedIndex, idMatch, searchCatalog, toSearchDoc } from "./search-index";

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
  // One map in both forms: DM is added to the ID once a publication goes digital.
  pub("M-205", "Geologic Map of the Gunlock Quadrangle", { "ugs:pub_type": "Map" }),
  pub("M-205DM", "Geologic Map of the Gunlock Quadrangle (GIS)", { "ugs:pub_type": "Map" }),
  // A map listed twice under case-only spellings (as in the live catalog), plus a lettered plate.
  pub("M-48", "Geologic Map of the Ogden Quadrangle", { "ugs:pub_type": "Map" }),
  pub("m-48", "Geologic Map of the Ogden Quadrangle", { "ugs:pub_type": "Map" }),
  pub("M-48A", "Geologic Map of the Ogden Quadrangle, Plate A", { "ugs:pub_type": "Map" }),
  pub("MP-173", "Selected papers on the Uinta Basin", { "ugs:pub_type": "Miscellaneous Publication" }),
  pub("CR-91-14DF", "Engineering geology of the Jordan Narrows", { "ugs:pub_type": "Contract Report" }),
  // Different files whose ids only differ by a hyphen (both live in the Mining District series).
  pub("MD-86-7", "Sage Plains Drill Hole Salt Wash Member Thickness", { "ugs:pub_type": "Mining District Files" }),
  pub("MD-867", "Beryl Mine, Davis County, Utah - Beryllium", { "ugs:pub_type": "Mining District Files" }),
];

const catalogIndex = () => buildIndex([], CATALOG.map(([coll, doc]) => toSearchDoc(coll, doc))).index;
const search = (q: string): string[] => searchCatalog(catalogIndex(), q).map((h) => String(h.id));

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
  ])("%s ranks that publication first", (q, want) => {
    expect(search(q)[0]).toBe(want);
  });

  it("does not fuzzy-match a series number to its neighbours", () => {
    // 773 and 791 are one digit off 771: close for a typo allowance, but a different publication.
    expect(search("OFR-771")).toEqual(["ugs-publications/OFR/OFR-771DM"]);
  });

  it("names no item when an ID fits more than one", () => {
    expect(idMatch(catalogIndex(), "MD-867")).toBeUndefined();
    expect(idMatch(catalogIndex(), "OFR-771")?.id).toBe("ugs-publications/OFR/OFR-771DM");
  });

  it("names the exact form first when a publication exists with and without DM", () => {
    expect(idMatch(catalogIndex(), "M-205")?.id).toBe("ugs-publications/M/M-205");
    expect(idMatch(catalogIndex(), "M-205DM")?.id).toBe("ugs-publications/M/M-205DM");
    expect(search("M-205")).toContain("ugs-publications/M/M-205DM");
  });

  it("stops at an ambiguous whole ID instead of trying the form without letters", () => {
    // M-48 and m-48 both match whole; M-48A would match without its letter, but is a different plate.
    expect(idMatch(catalogIndex(), "M-48")).toBeUndefined();
  });

  it("names no item for a number alone", () => {
    // 773 is only OFR-773's number, but a bare number may be a year or a count, not a citation.
    expect(idMatch(catalogIndex(), "773")).toBeUndefined();
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

describe("the shared catalog index", () => {
  it("adds newly streamed items to the same index instead of rebuilding it", () => {
    const ref = ([coll, doc]: (typeof CATALOG)[number]) => ({ collId: coll, href: `${coll}/${doc.id}/${doc.id}.json`, data: doc });
    const first = sharedIndex("a", CATALOG.slice(0, 2).map(ref));
    const grown = sharedIndex("b", CATALOG.map(ref));
    expect(grown.index).toBe(first.index);
    expect(grown.docs).toHaveLength(CATALOG.length);
    expect(searchCatalog(grown.index, String(CATALOG.at(-1)![1].id)).length).toBeGreaterThan(0);
    // An item going away rebuilds.
    expect(sharedIndex("c", CATALOG.slice(1).map(ref)).index).not.toBe(first.index);
  });
});
