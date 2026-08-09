// One place to get a file, and one list inside it. The item page used to offer the same GeoParquet
// four ways, and then a "Convert to" section under the list — so the reader had to know which
// formats were already on the CDN and which get built in the browser. That's our problem, not
// theirs: every format is one row, and how it gets made never comes up.
//
// The split that matters to a reader is what they DO with it: save a file (here) versus point a
// tool at a live URL (`endpoints-panel.tsx`, "Services"). Assets that are read over HTTP rather
// than saved — PMTiles, the GL style, DuckLake — belong there, not in this list.
import { useState } from "react";

import { type ExportFormat, exportItem, FORMATS, shapefileWarnings, type ShapefileWarnings } from "./download";
import { type Asset, parquetAsset, type StacDoc } from "./stac";
import { C } from "./ui";
import { UiSelect } from "./ui/select";

// Read by URL, not saved: these live under Services. Everything else the item publishes is a file.
const SERVICE_KEYS = new Set(["pmtiles", "style", "xyz", "ducklake", "tiles"]);

const EPSG_ITEMS = [
  { value: "4326", label: "WGS 84 (EPSG:4326)" },
  { value: "26912", label: "NAD83 / UTM 12N (EPSG:26912)" },
  { value: "32612", label: "WGS84 / UTM 12N (EPSG:32612)" },
  { value: "3857", label: "Web Mercator (EPSG:3857)" },
  { value: "other", label: "Other (any EPSG)…" },
];

// What each format is FOR — the question a reader actually has in front of ten of them. GeoJSON's
// note is load-bearing too: it's WGS 84 by spec, so the CRS picker below can't move it.
const HINTS: Record<ExportFormat, string> = {
  shp: "ArcMap · universal",
  gpkg: "QGIS · ArcGIS Pro",
  gdb: "ArcGIS Pro",
  fgb: "streaming · web",
  geojson: "web · always WGS 84",
  csv: "spreadsheet · WKT geometry",
};

/** `…/thing.parquet?x=1` → `parquet`. The file's own extension beats its mime type as a label. */
const extOf = (href: string) => href.split(/[?#]/)[0].split(".").pop()?.toLowerCase().slice(0, 8);

const isParquet = (a: Asset) =>
  /parquet/i.test(String(a.type ?? "")) || /parquet/i.test(String(a.href ?? ""));

/** The item's own files. `roles:["related"]` assets (UCRC boxes/photos/attachments) are left out:
 *  the Related tables section already offers each one next to its View/Gallery buttons. */
function fileAssets(item: StacDoc): [string, Asset][] {
  return Object.entries(item.assets ?? {})
    .filter(([key, a]) => !SERVICE_KEYS.has(key) && !a.roles?.includes("related"));
}

// Tiles, not full-width rows: ten rows across a wide panel strand the download arrow half a screen
// from the name it belongs to, and give the eye no shape to scan.
const TILE = "flex items-start justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 " +
  "text-left text-sm text-foreground no-underline hover:border-primary hover:text-primary disabled:opacity-50";
const SUB = "mt-0.5 block text-xs font-normal text-muted-foreground";

export function DownloadsPanel({ item }: { item: StacDoc }) {
  const parquet = parquetAsset(item);
  const fullBbox = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [err, setErr] = useState<string>();
  const [clipOn, setClipOn] = useState(false);
  const [bbox, setBbox] = useState<[number, number, number, number]>(fullBbox ?? [0, 0, 0, 0]);
  const [warn, setWarn] = useState<ShapefileWarnings | null>(null);  // shapefile pre-flight issues
  const [epsg, setEpsg] = useState(4326);                            // output CRS for the gdal formats
  const [customEpsg, setCustomEpsg] = useState(false);

  const files = fileAssets(item);
  if (!files.length && !parquet) return null;
  // Data files lead, the formats we build follow, and sidecars (ISO metadata, readme) trail.
  const [data, sidecars] = [files.filter(([, a]) => isParquet(a)), files.filter(([, a]) => !isParquet(a))];

  const doExport = async (fmt: ExportFormat) => {
    setBusy(fmt);
    try {
      await exportItem(parquet!.href, String(item.id ?? "export"), fmt, clipOn ? bbox : undefined, epsg);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const run = async (fmt: ExportFormat) => {
    setErr(undefined);
    setWarn(null);
    // Shapefile pre-flight: warn before handing over a silently-mangled file.
    if (fmt === "shp") {
      setBusy(fmt);
      try {
        const w = await shapefileWarnings(parquet!.href, clipOn ? bbox : undefined);
        if (w.any) { setWarn(w); setBusy(null); return; }
      } catch { /* check failed → just proceed to the export */ }
    }
    await doExport(fmt);
  };

  const assetTile = ([key, a]: [string, Asset]) => (
    <a key={key} href={a.href} target="_blank" rel="noopener" className={TILE}>
      <span className="font-medium">
        {a.title ?? key}
        <span className={SUB}>{extOf(a.href)}</span>
      </span>
      <span className="shrink-0 text-primary">↓</span>
    </a>
  );

  const labels = ["W", "S", "E", "N"];
  return (
    <section className="mt-3 rounded-lg border border-border bg-muted p-3">
      <h3 className="mb-1.5 text-sm font-semibold text-muted-foreground">Downloads</h3>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {data.map(assetTile)}
        {parquet && FORMATS.map((f) => (
          <button key={f.id} disabled={busy !== null} onClick={() => run(f.id)} className={TILE}>
            <span className="font-medium">
              {f.label}
              <span className={SUB}>{HINTS[f.id]}</span>
            </span>
            <span className="shrink-0 text-primary">{busy === f.id ? "…" : "↓"}</span>
          </button>
        ))}
        {sidecars.map(assetTile)}
      </div>
      {busy && <p className={`mt-1.5 ${C.muted}`}>preparing in your browser · the first one loads DuckDB (~a few MB)</p>}

      {/* Collapsed by default: the common path is pick a format and go, not reproject. */}
      {parquet && (
        <details className="mt-2 border-t border-border pt-2">
          <summary className="cursor-pointer text-sm text-muted-foreground">Projection &amp; area</summary>
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
            <label className="flex items-center gap-1">
              Output CRS
              <UiSelect value={customEpsg ? "other" : String(epsg)} className="px-1.5 py-0.5"
                onValueChange={(v) => {
                  if (v === "other") setCustomEpsg(true);
                  else { setCustomEpsg(false); setEpsg(Number(v)); }
                }}
                items={EPSG_ITEMS} />
            </label>
            {customEpsg && (
              <label className="flex items-center gap-1">
                EPSG:
                <input type="number" min={1024} max={999999} value={epsg} autoFocus
                  onChange={(e) => setEpsg(Number(e.target.value))}
                  className="w-24 rounded border border-input bg-card px-1.5 py-0.5 text-foreground" />
              </label>
            )}
          </div>
          {fullBbox && (
            <div className="mt-2 text-sm">
              <label className="flex items-center gap-1.5 text-muted-foreground">
                <input type="checkbox" checked={clipOn} onChange={(e) => setClipOn(e.target.checked)} />
                Clip to an area (bbox, EPSG:4326)
              </label>
              {clipOn && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {bbox.map((v, i) => (
                    <label key={i} className="flex items-center gap-1 text-muted-foreground">
                      {labels[i]}
                      <input type="number" step="0.01" value={v}
                        onChange={(e) => setBbox((b) => b.map((x, j) => (j === i ? Number(e.target.value) : x)) as typeof b)}
                        className="w-24 rounded border border-input bg-card px-1.5 py-0.5 text-foreground" />
                    </label>
                  ))}
                  <button onClick={() => setBbox(fullBbox)} className="text-primary">reset</button>
                </div>
              )}
            </div>
          )}
        </details>
      )}
      {err && <div className="mt-1.5 text-sm text-destructive">Download failed: {err}</div>}

      {warn && (
        <div className="mt-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-sm">
          <div className="font-semibold text-amber-700 dark:text-amber-400">Shapefile will mangle this data</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-foreground">
            {warn.mixedGeometry.length > 0 && (
              <li><b>Mixed geometry</b> ({warn.mixedGeometry.join(", ").toLowerCase()}) — a shapefile holds one geometry type; the others get dropped. Use GeoPackage.</li>
            )}
            {warn.longNames.length > 0 && (
              <li><b>{warn.longNames.length} field name{warn.longNames.length === 1 ? "" : "s"} over 10 chars</b> get truncated (e.g. <code>{warn.longNames[0]}</code> → <code>{warn.longNames[0].slice(0, 10)}</code>).</li>
            )}
            {warn.collisions.length > 0 && (
              <li><b>Field-name collisions</b> after truncation — <code>{warn.collisions[0][0]}</code> &amp; <code>{warn.collisions[0][1]}</code> collapse to the same name (data loss).</li>
            )}
            {warn.tooManyFields && <li><b>{warn.fieldCount} fields</b> exceeds the 255-field shapefile limit.</li>}
            {warn.over2gb && <li><b>~{(warn.estBytes / 1024 ** 3).toFixed(1)} GB estimated</b> — over the 2 GB shapefile limit (estimate; export may fail).</li>}
          </ul>
          <div className="mt-2 flex flex-wrap gap-2">
            <button onClick={() => { setWarn(null); doExport("gpkg"); }}
              className="rounded border border-border bg-primary px-2 py-0.5 text-primary-foreground hover:opacity-90">
              Use GeoPackage instead
            </button>
            <button onClick={() => { setWarn(null); doExport("shp"); }}
              className="rounded border border-border bg-card px-2 py-0.5 text-foreground hover:border-primary">
              Download shapefile anyway
            </button>
            <button onClick={() => setWarn(null)} className="text-muted-foreground hover:underline">Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}
