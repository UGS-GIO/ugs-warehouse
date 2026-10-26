import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseLatLon } from "@/map/coords";
import { parsePlss } from "@/map/plss";
import { type Gazetteer, quadSearch } from "@/map/quad-search";
import { SEARCH_EXAMPLES } from "./search-examples";

const example = (kind: string) => SEARCH_EXAMPLES.find((e) => e.kind === kind)!.text;

describe("SEARCH_EXAMPLES", () => {
  it("are found as the kind they name", async () => {
    const g: Gazetteer = JSON.parse(readFileSync(new URL("../../public/utah-quads.json", import.meta.url), "utf8"));
    expect((await quadSearch(g))(example("Quadrangle"))[0]).toMatchObject({ name: "Dromedary Peak", scale: "24k" });
    expect(parsePlss(example("PLSS township and section"))).toEqual({ township: "T3S R2E", section: 12 });
    expect(parseLatLon(example("Coordinates"))).toMatchObject({ lat: 40.57, lon: -111.65 });
  });
});
