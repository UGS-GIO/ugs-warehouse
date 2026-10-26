import { describe, expect, it } from "vitest";

import type { ItemRef } from "./browse";
import { cardText } from "./card-text";

const item = (collId: string, id: string, props: Record<string, unknown>): ItemRef =>
  ({ collId, href: `https://x/${collId}/${id}/${id}.json`, data: { id, properties: props } } as ItemRef);

describe("cardText", () => {
  it("leads a publication with what tells it apart, then id · year · scale · author", () => {
    const c = cardText(item("ugs-publications/I", "I-117", {
      title: "Photogeologic map of the Moab-12 quadrangle, Grand County, Utah", "ugs:series_id": "I-117",
      "ugs:pub_type": "Miscellaneous Investigations Series Map", datetime: "1955-01-01T00:00:00Z",
      "ugs:scale": "1:24,000", "ugs:author": "V.H. Sable",
    }));
    expect(c).toEqual({
      heading: "Moab-12 quadrangle", sub: "Photogeologic map · Grand County",
      meta: ["I-117", "1955", "1:24,000", "Sable"],
      full: "Photogeologic map of the Moab-12 quadrangle, Grand County, Utah", interim: false,
    });
  });

  it("shows a title it can't split in full, with its type, and says when a pub is undated", () => {
    const c = cardText(item("ugs-publications/B", "B-119", {
      title: "Petroleum resources of the Paradox Basin", "ugs:series_id": "B-119", "ugs:pub_type": "Bulletin",
    }));
    expect(c.heading).toBe("Petroleum resources of the Paradox Basin");
    expect(c.sub).toBe("Bulletin");
    expect(c.full).toBeUndefined();
    expect(c.meta).toEqual(["B-119", "Undated"]);
  });

  it("flags an interim map", () => {
    expect(cardText(item("ugs-publications/OFR", "OFR-322", {
      title: "Interim geologic map of the Moab quadrangle, Grand County, Utah", "ugs:series_id": "OFR-322",
    })).interim).toBe(true);
  });

  it("leaves a layer's title whole", () => {
    const c = cardText(item("ugs-serving-topics/hazards", "hazards_qfaults", {
      title: "Quaternary faults", "ugs:topic": "hazards", "ugs:dbt_schema": "hazards",
    }));
    expect(c).toEqual({ heading: "Quaternary faults", sub: "hazards", meta: [], interim: false });
  });
});
