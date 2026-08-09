// "How it all fits" — the end-to-end platform architecture + build status, for orientation
// and presentations. Content is hand-verified against the warehouse code (not just the docs,
// which drift); each layer carries an honest maturity badge. The flow diagram is mermaid,
// lazy-loaded only when this page opens (keeps it out of the main bundle).
import { useEffect, useRef, useState } from "react";
import { useIsDark } from "./theme";

type Status = "done" | "partial" | "planned";

const BADGE: Record<Status, { label: string; cls: string }> = {
  done: { label: "Built", cls: "bg-[#1a7f37] text-white" },
  partial: { label: "Partial", cls: "bg-[#9a6700] text-white" },
  planned: { label: "Not yet", cls: "bg-muted text-muted-foreground border border-border" },
};

function Badge({ status }: { status: Status }) {
  const b = BADGE[status];
  return <span className={`ml-2 rounded px-1.5 py-0.5 text-xs font-medium align-middle ${b.cls}`}>{b.label}</span>;
}

// The platform flow. Mermaid `flowchart`. classDef colors mirror the status badges so the
// diagram and the cards below tell the same story at a glance.
const DIAGRAM = `flowchart TB
  subgraph UP["① Upstream — ugs-ingest / dataELT"]
    direction TB
    ELT["dataELT medallion<br/>bronze → silver → gold (dbt)"]:::done
    PG["Cloud SQL · Postgres seamlessgeolmap<br/>{schema}.{topic}_current serving tables"]:::done
    ELT --> PG
  end
  PG -->|"② Pub/Sub #418<br/>{schema, topic}"| SVC
  subgraph WH["③ Warehouse — ugs-warehouse"]
    direction TB
    SVC["ugs-warehouse-service<br/>Cloud Run push handler"]:::done
    TR["DuckDB transform<br/>reproject → EPSG:4326 · hilbert sort"]:::done
    SVC --> TR
    TR --> DL["DuckLake table"]:::done
    TR --> GP["GeoParquet<br/>latest + dated"]:::done
    TR --> PM["PMTiles"]:::done
    TR --> ST["STAC item"]:::done
  end
  subgraph STY["④ Styling — ugs-styles"]
    SM["styles manifest (CDN)"]:::done
    RS["restyle job<br/>rebind ugs:renders by STAC item id"]:::done
    SM --> RS
  end
  RS -->|"ugs:renders + style asset"| ST
  subgraph PUBS["⑤ Publications"]
    MY["MySQL pubsdb<br/>source of truth"]:::planned
    PGM["Postgres mirror<br/>via DuckDB postgres ext"]:::planned
    CSV["vendored CSV snapshot<br/>⚠ prod default · can go stale"]:::partial
    HV["harvest · GDAL → COG"]:::done
    PI["pubs ingest → STAC<br/>3 collections"]:::done
    MY -. "manual export" .-> CSV
    MY -. "PUBS_DB_URL (unset in prod)" .-> PI
    PGM -. "PUBS_DB_URL (unset in prod)" .-> PI
    CSV --> PI
    HV --> PI
  end
  PI --> ST
  subgraph SRV["⑥ Storage + Serving + Consumers"]
    GCS["GCS bucket (private)"]:::done
    CDN["CDN · maps-assets.geology.utah.gov"]:::done
    VW["STAC viewer (this app)"]:::done
    FS["OGC API Features<br/>duckdb_featureserv / GeoParquet"]:::done
    PGFS["pg_featureserv / Postgres<br/>parallel · config stale"]:::partial
    RAS["raster consumer<br/>blocked · ugs-ingest #169"]:::planned
  end
  DL --> GCS
  GP --> GCS
  PM --> GCS
  ST --> GCS
  GCS --> CDN
  CDN --> VW
  CDN --> FS
  PG --> PGFS
  FS --> POOL["ArcGIS Pro · QGIS · federation"]:::done
  PGFS --> POOL
  classDef done fill:#1a7f37,stroke:#0b4a20,color:#fff;
  classDef partial fill:#9a6700,stroke:#5c3d00,color:#fff;
  classDef planned fill:#6e7781,stroke:#424a53,color:#fff,stroke-dasharray:4 3;
`;

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
          theme: dark ? "dark" : "default",
          securityLevel: "strict",
          flowchart: { htmlLabels: true, curve: "basis" },
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
  return <div ref={ref} className="w-full overflow-x-auto [&_svg]:!max-w-none [&_svg]:w-full [&_svg]:h-auto" />;
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
      "`gwportal` lives in a separate DB and is intentionally skipped.",
    ],
    note: "The schema is `gengis`, not `gen_gis` — a recent fix (commit d9e40ca); a prod reingest is still pending so gengis topics flow through.",
  },
  {
    n: "②", title: "Ingest trigger — Pub/Sub #418", status: "done",
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
    ],
    note: "Unstamped geometry (SRID 0) now errors loudly instead of silently assuming 4326 — a recent guard (PR ugs-warehouse#2).",
  },
  {
    n: "③", title: "Four sinks per topic", status: "done",
    lead: "Each topic fans out to four artifacts, all derive-from-truth and idempotent.",
    points: [
      "DuckLake table — lakehouse format, catalog in Postgres, data on GCS.",
      "GeoParquet — a mutable `latest` pointer + immutable dated snapshots (citable), bbox covering columns.",
      "PMTiles — vector tiles via tippecanoe (`-r1`, keeps every point at every zoom).",
      "STAC item — the discovery + linking layer that ties the others together.",
    ],
  },
  {
    n: "④", title: "Styling — ugs-styles", status: "done",
    lead: "Cartographers work in the ugs-styles repo; styles bind into the catalog by STAC item id — one namespace, no drift.",
    points: [
      "ugs-styles compiles to a manifest published on the same CDN.",
      "At STAC emit, a matching style attaches a `ugs:renders` block (MapLibre GL style URL) + a `style` asset.",
      "The `restyle` job rebinds renders without a reingest — seconds, no DB, no tiles rebuilt.",
    ],
  },
  {
    n: "⑤", title: "Publications", status: "partial",
    lead: "Publications are a second producer into the same STAC catalog: scanned geologic maps become COGs + footprints, routed into three collections.",
    points: [
      "Harvest pipeline (GDAL) turns publication zips into validated Cloud-Optimized GeoTIFFs.",
      "Collections: ugs-publications (UGS/UGMS + USGS Utah), ugs-mining-district-files, ugs-external.",
      "Metadata source is pluggable (`PUBS_DB_URL`): live MySQL, live Postgres (via the DuckDB postgres extension), or the vendored CSV snapshot — CSV is the prod default.",
    ],
    note: "Honest gap: the `PUBS_DB_URL` plumbing supports live MySQL or Postgres, but prod leaves it unset — so the pipeline reads the vendored CSV snapshot checked into the repo. That snapshot is point-in-time and goes stale as upstream changes. Wiring a live source (MySQL or a Postgres mirror) is the open item.",
  },
  {
    n: "⑥", title: "Storage, serving & consumers", status: "done",
    lead: "Artifacts land in one private GCS bucket and are served read-only through the maps-assets CDN, which preserves object paths.",
    points: [
      "Static surfaces (no server): GeoParquet, PMTiles, COG, STAC JSON — read directly from the CDN.",
      "STAC viewer (this app): catalog browse + map + COG preview + client-side export.",
      "OGC API Features for ArcGIS Pro / QGIS: `duckdb_featureserv` over the GeoParquet on the CDN, scale-to-zero.",
    ],
    note: "A second OGC path exists — `pg_featureserv` (api/) straight over Postgres — but its config has drifted (still lists `gen_gis`, only 5 of 7 schemas). It needs reconciling with, or retiring in favour of, the duckdb_featureserv tier.",
  },
];

const ROADMAP: { status: Status; text: string }[] = [
  { status: "planned", text: "Raster consumer — blocked on ugs-ingest #169 (open draft). The promote step raises NotImplementedError until #169 defines the raw.raster_catalog contract and cross-bucket copy." },
  { status: "planned", text: "Raster ingest (soil-water time-series + one-off rasters) — design only, not implemented." },
  { status: "partial", text: "STAC `datetime` is ingest time, not data-validity time — waiting on an upstream validity timestamp." },
  { status: "partial", text: "Metadata export: ISO 19139 sidecar done for vector + pubs; FGDC variant and raster extension still open." },
  { status: "partial", text: "Live publications source (MySQL or Postgres mirror) instead of the vendored CSV snapshot." },
  { status: "planned", text: "Publish allowlist — explicit per-layer control over what the warehouse exposes (parked)." },
];

const C = { wrap: "w-full px-4 py-6 sm:px-6 lg:px-10" };

export function Architecture() {
  return (
    <div className={C.wrap}>
      <h1 className="text-2xl font-bold">Platform Architecture</h1>
      <p className="mt-2 max-w-[75ch] text-muted-foreground">
        How geology data flows from the source databases, through the warehouse, to the maps and
        services people use. The warehouse forks dataELT's published gold tables and produces
        cloud-native artifacts — GeoParquet, PMTiles, DuckLake, COGs — tied together by a STAC catalog.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded" style={{ background: "#1a7f37" }} /> Built &amp; in production</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded" style={{ background: "#9a6700" }} /> Partial / has a known gap</span>
        <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded border border-border" style={{ background: "#6e7781" }} /> Not yet / blocked</span>
      </div>

      <section className="mt-6 rounded-lg border border-border bg-card p-4">
        <Mermaid chart={DIAGRAM} />
      </section>

      <h2 className="mt-8 text-lg font-semibold">The pipeline, layer by layer</h2>
      <div className="mt-3 grid gap-4 lg:grid-cols-2">
        {LAYERS.map((l, i) => (
          <section key={i} className="rounded-lg border border-border bg-card p-4">
            <h3 className="text-base font-semibold">
              <span className="mr-1.5 text-muted-foreground">{l.n}</span>{l.title}
              <Badge status={l.status} />
            </h3>
            <p className="mt-1.5 text-sm text-muted-foreground">{l.lead}</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {l.points.map((p, j) => <li key={j}>{p}</li>)}
            </ul>
            {l.note && (
              <p className="mt-2 rounded-md border-l-2 border-[#9a6700] bg-muted/40 px-3 py-2 text-sm">
                <strong className="text-accent">Note:</strong> {l.note}
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

      <p className="mt-6 text-xs text-muted-foreground">
        Verified against the warehouse source, not just the docs. Status reflects {__BUILD_DATE__} ({__BUILD_HASH__}).
      </p>
    </div>
  );
}
