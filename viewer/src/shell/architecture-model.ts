// The platform graph as data, and the mermaid emitter for it. Hand-typed mermaid is how this page
// went stale: the syntax and the facts were the same string, so nothing could check either. Nodes
// carry their status and their deployed unit (the Cloud Run service/job they are), which lets a
// test assert the page still covers everything cloudbuild.yaml deploys.

export type Status = "done" | "partial" | "planned";

export type Node = {
  id: string;
  label: string;          // `|` splits into lines
  status: Status;
  unit?: string;          // the Cloud Run service/job this node IS, when it is one
};

export type Edge = { from: string; to: string; label?: string; dashed?: boolean };

export type Flow = { title: string; wide?: boolean; nodes: Node[]; edges: Edge[] };

export const STATUS_COLOR: Record<Status, { fill: string; stroke: string; text: string }> = {
  done: { fill: "#1a7f37", stroke: "#0b4a20", text: "#fff" },
  partial: { fill: "#9a6700", stroke: "#5c3d00", text: "#fff" },
  planned: { fill: "#6e7781", stroke: "#424a53", text: "#fff" },
};

export const FLOWS: Flow[] = [
  {
    // The general shape only — five stages, no service names. Anyone who wants the steps inside a
    // stage reads the flows below; this one exists so the shape fits in one glance.
    title: "Overview", wide: true,
    nodes: [
      { id: "UP", label: "upstream|dataELT gold → serving tables", status: "done" },
      { id: "WH", label: "warehouse|Pub/Sub → DuckDB transform", status: "done" },
      { id: "OP", label: "other producers|rasters · publications · styles", status: "partial" },
      { id: "ART", label: "artifacts|DuckLake · GeoParquet · PMTiles · COG · STAC", status: "done" },
      { id: "AFT", label: "after publish|thumbnails · DuckLake expiry", status: "done" },
    ],
    edges: [
      { from: "UP", to: "WH" }, { from: "WH", to: "ART" },
      { from: "OP", to: "ART" }, { from: "ART", to: "AFT" },
    ],
  },
  {
    title: "Ingest → artifacts", wide: true,
    nodes: [
      { id: "ELT", label: "dataELT medallion|bronze → silver → gold", status: "done" },
      { id: "PG", label: "Postgres seamlessgeolmap|{schema}.{topic}_current", status: "done" },
      { id: "SVC", label: "warehouse service|Pub/Sub push handler", status: "done", unit: "ugs-warehouse-service" },
      { id: "TR", label: "DuckDB transform|reproject 4326 · hilbert · ugs_key", status: "done" },
      { id: "RC", label: "raster consume + promote|staged COG → public", status: "partial" },
      { id: "STY", label: "ugs-styles|manifest · sprites · glyphs", status: "done" },
      { id: "RS", label: "restyle job|rebind ugs:renders", status: "done", unit: "ugs-warehouse-restyle" },
      { id: "TH", label: "topic thumbnails|styled PMTiles → WebP", status: "done", unit: "ugs-topics-thumbs" },
      { id: "MT", label: "DuckLake maintenance", status: "done", unit: "ugs-warehouse-ducklake-maintain" },
      { id: "OUT", label: "DuckLake · GeoParquet|PMTiles · STAC item", status: "done" },
    ],
    edges: [
      { from: "ELT", to: "PG" }, { from: "PG", to: "SVC", label: "{schema, topic}" },
      { from: "SVC", to: "TR" }, { from: "TR", to: "OUT" }, { from: "RC", to: "OUT" },
      { from: "STY", to: "RS" }, { from: "RS", to: "OUT", label: "ugs:renders" },
      { from: "OUT", to: "TH" }, { from: "OUT", to: "MT" },
    ],
  },
  {
    title: "Artifacts → services", wide: true,
    nodes: [
      { id: "OUT", label: "artifacts|GeoParquet · PMTiles · COG · STAC", status: "done" },
      { id: "GCS", label: "GCS bucket (private)", status: "done" },
      { id: "CDN", label: "CDN|maps-assets.geology.utah.gov", status: "done" },
      { id: "EXT", label: "external catalogs (USWB)", status: "done" },
      { id: "VW", label: "STAC viewer|browse · map · datacubes · export", status: "done" },
      { id: "FS", label: "OGC API Features|duckdb_featureserv", status: "done", unit: "ugs-warehouse-features" },
      { id: "TS", label: "tiles service|XYZ · MapLibre styles · Esri VTS", status: "done", unit: "ugs-warehouse-tiles" },
      { id: "PGFS", label: "pg_featureserv|parallel · config stale", status: "partial", unit: "ugs-warehouse-api" },
      { id: "POOL", label: "ArcGIS Pro · QGIS · AGOL", status: "done" },
    ],
    edges: [
      { from: "OUT", to: "GCS" }, { from: "GCS", to: "CDN" },
      { from: "EXT", to: "CDN", label: "federated into the root" },
      { from: "CDN", to: "VW" }, { from: "CDN", to: "FS" }, { from: "CDN", to: "TS" },
      { from: "FS", to: "POOL" }, { from: "TS", to: "POOL" }, { from: "PGFS", to: "POOL" },
    ],
  },
  {
    title: "Publications",
    nodes: [
      { id: "MY", label: "live pubs DB|MySQL / Postgres mirror", status: "planned" },
      { id: "CSV", label: "vendored CSV snapshot|⚠ prod default", status: "partial" },
      { id: "HV", label: "harvest|GDAL → COG · mosaics", status: "done", unit: "ugs-geolmap-mosaics" },
      { id: "TD", label: "3D pipeline|GeMS → glTF", status: "done", unit: "ugs-pubs-threed" },
      { id: "PI", label: "pubs ingest → STAC", status: "done", unit: "ugs-pubs-ingest" },
      { id: "IX", label: "search indexes|FTS + embeddings", status: "done", unit: "ugs-pubs-fts" },
    ],
    edges: [
      { from: "MY", to: "PI", label: "PUBS_DB_URL unset in prod", dashed: true },
      { from: "CSV", to: "PI" }, { from: "HV", to: "PI" }, { from: "TD", to: "PI" }, { from: "PI", to: "IX" },
    ],
  },
  {
    title: "Review path",
    nodes: [
      { id: "PGR", label: "_review serving tables", status: "done" },
      { id: "RVI", label: "ingest → review/ prefixes", status: "done", unit: "ugs-warehouse-ingest-review" },
      { id: "RVA", label: "review app (IAP)|prod ∪ review · comments", status: "done" },
      { id: "PROM", label: "promote → public|threads become history", status: "done" },
    ],
    edges: [{ from: "PGR", to: "RVI" }, { from: "RVI", to: "RVA" }, { from: "RVA", to: "PROM" }],
  },
];

const classDefs = (): string =>
  (Object.keys(STATUS_COLOR) as Status[]).map((s) => {
    const c = STATUS_COLOR[s];
    const dash = s === "planned" ? ",stroke-dasharray:4 3" : "";
    return `  classDef ${s} fill:${c.fill},stroke:${c.stroke},color:${c.text},rx:4,ry:4${dash};`;
  }).join("\n");

/** One flow → mermaid source. `|` in a label becomes a line break. */
export function toMermaid(flow: Flow): string {
  const nodes = flow.nodes.map((n) => `  ${n.id}["${n.label.split("|").join("<br/>")}"]:::${n.status}`);
  // Mermaid's four edge spellings — a label changes the arrow, it isn't just inserted into it.
  const edges = flow.edges.map((e) => {
    if (e.dashed) return e.label ? `  ${e.from} -. "${e.label}" .-> ${e.to}` : `  ${e.from} -.-> ${e.to}`;
    return e.label ? `  ${e.from} -- "${e.label}" --> ${e.to}` : `  ${e.from} --> ${e.to}`;
  });
  return [`flowchart LR`, ...nodes, ...edges, classDefs()].join("\n");
}
