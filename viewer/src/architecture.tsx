// "How it all fits" — the end-to-end platform architecture + build status, for orientation
// and presentations. Content is hand-verified against the warehouse code (not just the docs,
// which drift); each layer carries an honest maturity badge. The flow diagram is mermaid,
// lazy-loaded only when this page opens (keeps it out of the main bundle).
import { useEffect, useRef, useState } from "react";
import { FLOWS, type Status, STATUS_COLOR, toMermaid } from "./architecture-model";
import { PageHero } from "./page-hero";
import { useIsDark } from "./theme";

const BADGE: Record<Status, string> = { done: "Built", partial: "Partial", planned: "Not yet" };

function Badge({ status }: { status: Status }) {
  const c = STATUS_COLOR[status];
  return (
    <span className="ml-2 rounded px-1.5 py-0.5 align-middle text-xs font-medium"
      style={{ background: c.fill, color: c.text }}>{BADGE[status]}</span>
  );
}

function Mermaid({ chart }: { chart: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const dark = useIsDark();
  const [err, setErr] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "base",
          securityLevel: "strict",
          fontFamily: '"Source Sans 3 Variable", "Source Sans 3", system-ui, sans-serif',
          themeVariables: {
            fontSize: "14px",
            background: "transparent",
            lineColor: dark ? "#8b949e" : "#57606a",
            textColor: dark ? "#f5f5f5" : "#333",
            // Subgraphs and any unclassed node sit on the app's card colour, not mermaid's mauve.
            primaryColor: dark ? "#2a2a2a" : "#f6f8fa",
            primaryBorderColor: dark ? "#474747" : "#d7d7d7",
            primaryTextColor: dark ? "#f5f5f5" : "#333",
          },
          flowchart: { htmlLabels: true, curve: "basis", nodeSpacing: 28, rankSpacing: 46, padding: 10 },
        });
        // unique id per render so theme switches re-render cleanly
        const { svg } = await mermaid.render(`arch-${dark ? "dark" : "light"}-${Date.now()}`, chart);
        if (!cancelled && ref.current) ref.current.innerHTML = svg;
      } catch (e) {
        if (!cancelled) setErr(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [chart, dark]);

  if (err) return <pre className="overflow-auto rounded-md border border-destructive p-3 text-xs text-destructive">{err}</pre>;
  // stretch the SVG to fill the (wide) container instead of floating at its natural size
  return <div ref={ref} className="w-full overflow-x-auto [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full" />;
}

type Layer = {
  n: string; title: string; status: Status; lead: string;
  points: string[]; note?: string;
};

const LAYERS: Layer[] = [
  {
    n: "①", title: "Upstream — ugs-ingest / dataELT", status: "done",
    lead: "The warehouse does not own the data. dataELT (the ugs-ingest project) runs a dbt medallion — bronze → silver → gold — and publishes gold as Postgres serving tables.",
    points: [
      "Source of truth: Cloud SQL Postgres `seamlessgeolmap`, one `{schema}.{topic}_current` table per topic.",
      "Mart schemas the warehouse scans: hazards, emp, gengis, wetlands, mapping, geochron, boreholes.",
      "`_review` tables feed the parallel review catalog (⑧); `gwportal` lives in another DB and is skipped.",
    ],
  },
  {
    n: "②", title: "Ingest trigger — Pub/Sub", status: "done",
    lead: "Every time dataELT promotes a `_current` table it publishes a `{schema, topic}` message; the warehouse reacts. No polling, no schedule.",
    points: [
      "Topic `ugs-warehouse-ingest` → push subscription → the private `ugs-warehouse-service` Cloud Run endpoint.",
      "A topic whose schema isn't a known mart is acked + skipped (no Pub/Sub retry storm).",
      "The same code path also runs as an `--all` batch job for a full reingest.",
    ],
  },
  {
    n: "③", title: "Warehouse transform", status: "done",
    lead: "One DuckDB streaming pass turns a Postgres serving table into cloud-native artifacts — no rows ever materialize in Python.",
    points: [
      "Reproject every source CRS → EPSG:4326 (the uniform publish CRS).",
      "Hilbert-sort rows so Parquet row-groups bbox-prune well (locality doubles as the spatial index).",
      "Memory-capped (spills to disk) to survive the free-tier container limit.",
      "Rasters take a second path: consume a staged COG, promote it to the public bucket, write its STAC item.",
    ],
    note: "Serving topics nest one collection per mart schema under `ugs-serving-topics`, with a rollup items index so one fetch still gets everything.",
  },
  {
    n: "④", title: "Styling — ugs-styles", status: "done",
    lead: "Cartography lives in its own repo and binds to the catalog by STAC item id — no parallel style namespace, and no restyling means no reingest.",
    points: [
      "ugs-styles publishes a manifest, sprite sheets and glyph fontstacks to the CDN; a `v*` tag is what publishes.",
      "The `ugs-warehouse-restyle` job rewrites `ugs:renders` on the items whose bindings changed — seconds, no data reingest.",
      "Label layers carry their glyph URL, so the tiles service, the viewer and ArcGIS all resolve the same fonts.",
    ],
  },
  {
    n: "⑤", title: "Publications", status: "partial",
    lead: "The publication catalog is harvested rather than pushed: plates become COGs, GeMS packages become 3D glTF, and the text becomes search indexes.",
    points: [
      "Harvest builds COGs (and per-scale seamless mosaics) from the published plates.",
      "Pubs ingest writes the STAC items; separate jobs build cover thumbnails, the citation graph, FTS and embedding indexes.",
      "Those indexes are what the Search tab queries — DuckDB files read straight off the CDN.",
    ],
    note: "Prod still reads a vendored CSV snapshot of the publications database, so upstream edits don't reach the catalog until someone re-exports it (#121).",
  },
  {
    n: "⑥", title: "Storage, CDN + serving", status: "done",
    lead: "Artifacts land in one private GCS bucket and are served read-only through the maps-assets CDN, which preserves object paths.",
    points: [
      "Static surfaces (no server): GeoParquet, PMTiles, COG, STAC JSON — read directly from the CDN.",
      "OGC API Features for ArcGIS Pro / QGIS: `duckdb_featureserv` over the GeoParquet on the CDN, scale-to-zero.",
      "`ugs-warehouse-tiles`: XYZ tiles, ready-to-use MapLibre styles, and an Esri VectorTileServer facade so AGOL and Pro can add a layer at all.",
      "External catalogs (USWB) are federated in as children of the root, so one catalog URL covers them too.",
    ],
    note: "A second OGC path exists — `pg_featureserv` (api/) straight over Postgres — but its config has drifted. It needs reconciling with, or retiring in favour of, the duckdb_featureserv tier.",
  },
  {
    n: "⑦", title: "Review path", status: "done",
    lead: "A parallel catalog built from `_review` tables under `review/` prefixes, behind IAP — so data can be checked before it is public.",
    points: [
      "Same pipeline, different suffix and output prefixes; the public catalog is untouched by it.",
      "The review app federates prod ∪ review and takes comments at item, column and row level.",
      "Promotion turns a review comment thread into read-only history rather than deleting it.",
    ],
  },
];

const ROADMAP: { status: Status; text: string }[] = [
  { status: "partial", text: "Raster consumer — the consume/promote code is on main; what's left is deploy provisioning on the work box (promote topic, push subscription, staged-bucket grant)." },
  { status: "planned", text: "Grouping model — dataset/collection/group spine so a project or a seamless mosaic is a first-class object (ugs-ingest #342)." },
  { status: "partial", text: "STAC `datetime` is ingest time, not data-validity time — waiting on an upstream validity timestamp." },
  { status: "partial", text: "Metadata export: ISO 19139 sidecar done for vector + pubs; FGDC variant and raster extension still open." },
  { status: "partial", text: "Live publications source (MySQL or Postgres mirror) instead of the vendored CSV snapshot (#121)." },
  { status: "planned", text: "Publish allowlist — explicit per-layer control over what the warehouse exposes, and a private tier for embargoed layers." },
];

const C = { wrap: "w-full px-4 py-6 sm:px-6 lg:px-10" };

export function Architecture() {
  return (
    <>
      <PageHero
        eyebrow="How it works"
        title="Platform Architecture"
        lead="How geology data flows from the source databases, through the warehouse, to the maps and services people use. The warehouse forks dataELT's published gold tables and produces cloud-native artifacts — GeoParquet, PMTiles, DuckLake, COGs — tied together by a STAC catalog."
      />
    <div className={C.wrap}>
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
        {([["done", "Built & in production"], ["partial", "Partial / has a known gap"],
           ["planned", "Not yet / blocked"]] as [Status, string][]).map(([s, label]) => (
          <span key={s} className="flex items-center gap-1.5">
            <span className="inline-block h-3 w-3 rounded" style={{ background: STATUS_COLOR[s].fill }} /> {label}
          </span>
        ))}
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-2">
        {FLOWS.map((flow) => (
          <section key={flow.title}
            className={`rounded-lg border border-border bg-card p-4 ${flow.wide ? "xl:col-span-2" : ""}`}>
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wider text-muted-foreground">{flow.title}</h3>
            <Mermaid chart={toMermaid(flow)} />
          </section>
        ))}
      </div>

      <h2 className="mt-8 text-lg font-semibold">The pipeline, layer by layer</h2>
      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        {LAYERS.map((l, i) => (
          <section key={i} className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-base font-semibold">
              <span className="text-muted-foreground">{l.n}</span> {l.title}
              <Badge status={l.status} />
            </h3>
            <p className="mt-1.5 text-sm text-muted-foreground">{l.lead}</p>
            <ul className="mt-2 space-y-1 text-sm">
              {l.points.map((p, j) => (
                <li key={j} className="flex gap-2">
                  <span className="text-muted-foreground">·</span>
                  <span>{p}</span>
                </li>
              ))}
            </ul>
            {l.note && (
              <p className="mt-2 rounded-md border-l-2 bg-muted/40 px-2.5 py-1.5 text-sm"
                style={{ borderColor: STATUS_COLOR.partial.fill }}>
                <strong style={{ color: STATUS_COLOR.partial.fill }}>Note:</strong> {l.note}
              </p>
            )}
          </section>
        ))}
      </div>

      <h2 className="mt-8 text-lg font-semibold">What's still open</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        The vector pipeline is end-to-end in production. The honest gaps:
      </p>
      <ul className="mt-3 space-y-2">
        {ROADMAP.map((r, i) => (
          <li key={i} className="flex items-start gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
            <span className="mt-0.5 shrink-0"><Badge status={r.status} /></span>
            <span>{r.text}</span>
          </li>
        ))}
      </ul>

      <p className="mt-6 text-sm text-muted-foreground">
        Verified against the warehouse source, not just the docs. Status reflects {__BUILD_DATE__} ({__BUILD_HASH__}).
      </p>
    </div>
    </>
  );
}
