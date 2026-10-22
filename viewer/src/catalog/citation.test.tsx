// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { collectionAsItem, type StacDoc } from "@/stac";
import { CiteMenu } from "./cite-menu";
import { bibEscape, bibtex, citationFields, plainCitation, ris, splitAuthors } from "./citation";
import authorCases from "./fixtures/authors.json";
import c140 from "./fixtures/C-140.collection.json";

const fields = () => citationFields(collectionAsItem(c140 as unknown as StacDoc).properties, "C-140");

afterEach(() => vi.restoreAllMocks());

describe("C-140 export", () => {
  it("splits the feed's mixed author string", () => {
    expect(fields().authors).toEqual(
      ["Andrew Rupke", "Stephanie E. Mills", "Michael D. Vanden Berg", "Noah Christensen", "Taylor Boden"]);
    expect(splitAuthors("Smith, John; Doe, Jane and Roe, Rick").names).toEqual(["Smith, John", "Doe, Jane", "Roe, Rick"]);
  });

  it.each(authorCases)("splits real feed authors: $raw", ({ raw, names, ambiguous }) => {
    expect(splitAuthors(raw)).toEqual({ names, ambiguous });
  });

  it("keeps an ambiguous string as one braced BibTeX author and one RIS AU line", () => {
    const f = citationFields({ title: "T", "ugs:series_id": "X-1", "ugs:author": "I.J. Witkind; assisted by H.T. Cantor, and G.R. Marshall" });
    expect(f.authorsAmbiguous).toBe(true);
    expect(bibtex(f)).toContain("author = {{I.J. Witkind; assisted by H.T. Cantor, and G.R. Marshall}}");
    expect(ris(f).split("\r\n").filter((l) => l.startsWith("AU"))).toHaveLength(1);
  });

  it("builds BibTeX as a techreport", () => {
    const b = bibtex(fields());
    expect(b).toMatch(/^@techreport\{UGS_C-140,/);
    expect(b).toContain("author = {Andrew Rupke and Stephanie E. Mills and Michael D. Vanden Berg and Noah Christensen and Taylor Boden}");
    expect(b).toContain("title = {Utah Mining 2025 - Metals, Industrial Minerals, Uranium, and Coal}");
    expect(b).toContain("year = {2026}");
    expect(b).toContain("institution = {Utah Geological Survey}");
    expect(b).toContain("type = {Circular}");
    expect(b).toContain("number = {C-140}");
    expect(b).toContain("doi = {10.34191/C-140}");
    expect(b).toContain("url = {https://doi.org/10.34191/C-140}");
    expect(b).not.toContain("pages");
    expect(b).not.toContain("howpublished");
  });

  it("builds RIS as a report", () => {
    expect(ris(fields()).split("\r\n")).toEqual([
      "TY  - RPRT", "AU  - Andrew Rupke", "AU  - Stephanie E. Mills", "AU  - Michael D. Vanden Berg",
      "AU  - Noah Christensen", "AU  - Taylor Boden",
      "TI  - Utah Mining 2025 - Metals, Industrial Minerals, Uranium, and Coal", "PY  - 2026",
      "PB  - Utah Geological Survey", "SN  - C-140", "DO  - 10.34191/C-140", "UR  - https://doi.org/10.34191/C-140",
      "ER  - ", "",
    ]);
  });

  it("uses sci:citation when present", () => {
    expect(plainCitation(fields())).toBe(c140["sci:citation"]);
  });
});

describe("fallback and escaping", () => {
  it("builds a plain citation from present fields only", () => {
    const f = citationFields({ title: "A Map", "ugs:author": "Doe, Jane", datetime: "1999-01-01T00:00:00Z",
      "ugs:pub_type": "Map", "ugs:series_id": "M-1" });
    expect(plainCitation(f)).toBe(
      "Doe, Jane, 1999, A Map: Utah Geological Survey Map M-1, https://doi.org/10.34191/M-1.");
    expect(bibtex(f)).toMatch(/^@misc\{UGS_M-1,/);
    expect(bibtex(f)).toContain("howpublished = {Utah Geological Survey Map M-1}");
    expect(bibtex(f)).not.toContain("institution");
    expect(ris(f)).toMatch(/^TY {2}- MAP/);
  });

  it("omits what the data lacks", () => {
    const f = citationFields({ title: "T", "ugs:series_id": "X-1", "ugs:date_unknown": true, datetime: "2000-01-01T00:00:00Z" });
    expect(plainCitation(f)).toBe("T: Utah Geological Survey X-1, https://doi.org/10.34191/X-1.");
  });

  it("leaves doi and url verbatim", () => {
    const b = bibtex(citationFields({ title: "T", "ugs:series_id": "X-1", "sci:doi": "10.34191/X_1%2" }));
    expect(b).toContain("doi = {10.34191/X_1%2}");
    expect(b).toContain("url = {https://doi.org/10.34191/X_1%2}");
  });

  it("flattens line breaks and tabs in RIS values", () => {
    const r = ris(citationFields({ title: "Line one\n  line\ttwo", "ugs:series_id": "X-1" }));
    expect(r).toContain("TI  - Line one line two\r\n");
  });

  it("escapes BibTeX specials", () => {
    expect(bibEscape("R&D {50%} a_b")).toBe("R\\&D \\{50\\%\\} a\\_b");
  });
});

describe("CiteMenu", () => {
  it("copies the citation and shows Copied", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<CiteMenu fields={fields()} className="x" />);
    await user.click(screen.getByRole("button", { name: /Cite as/ }));
    await user.click(await screen.findByRole("button", { name: "Copy citation" }));
    expect(writeText).toHaveBeenCalledWith(c140["sci:citation"]);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Copied to clipboard");
    expect(screen.getByRole("button", { name: "Download RIS" })).toBeTruthy();
  });

  it("shows an error when the clipboard fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    render(<CiteMenu fields={fields()} className="x" />);
    await user.click(screen.getByRole("button", { name: /Cite as/ }));
    await user.click(await screen.findByRole("button", { name: "Copy BibTeX" }));
    expect((await screen.findByRole("alert")).textContent).toContain("denied");
  });
});
