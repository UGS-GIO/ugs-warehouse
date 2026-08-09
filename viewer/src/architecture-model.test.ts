import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FLOWS, toMermaid } from "./architecture-model";

const repoFile = (rel: string) =>
  readFileSync(fileURLToPath(new URL(`../../${rel}`, import.meta.url)), "utf8");

describe("toMermaid", () => {
  it("emits nodes, edges and one classDef per status", () => {
    const src = toMermaid(FLOWS[0]);
    expect(src.startsWith("flowchart LR")).toBe(true);
    expect(src).toContain('SVC["warehouse service<br/>Pub/Sub push handler"]:::done');
    expect(src).toContain('PG -- "{schema, topic}" --> SVC');
    for (const s of ["done", "partial", "planned"]) expect(src).toContain(`classDef ${s} `);
  });

  it("draws a dashed edge for a link that isn't live", () => {
    const pubs = FLOWS.find((f) => f.title === "Publications")!;
    expect(toMermaid(pubs)).toContain('MY -. "PUBS_DB_URL unset in prod" .-> PI');
  });

  it("every edge points at a node that exists", () => {
    for (const flow of FLOWS) {
      const ids = new Set(flow.nodes.map((n) => n.id));
      for (const e of flow.edges) {
        expect(ids.has(e.from), `${flow.title}: ${e.from}`).toBe(true);
        expect(ids.has(e.to), `${flow.title}: ${e.to}`).toBe(true);
      }
    }
  });
});

// The page went stale because nothing failed when the platform grew: the tiles service, the review
// ingest and nine jobs shipped without ever reaching the diagram. This is that alarm.
describe("coverage of what we actually deploy", () => {
  const deployed = [...repoFile("cloudbuild.yaml").matchAll(/^\s+_[A-Z0-9_]+(?:SERVICE|JOB):\s*(\S+)/gm)]
    .map((m) => m[1]);
  const charted = new Set(FLOWS.flatMap((f) => f.nodes.map((n) => n.unit).filter(Boolean)));

  it("finds the deployed units in cloudbuild.yaml", () => {
    expect(deployed.length).toBeGreaterThan(8);
  });

  // Not every job earns a box (several are steps of one story), so this asserts the SURFACES —
  // anything a consumer talks to — are all on the page.
  it("charts every service a consumer can reach", () => {
    const services = deployed.filter((u) => /service|features|tiles|api/.test(u));
    for (const s of services) expect([...charted], `${s} missing from the diagrams`).toContain(s);
  });
});
