// Download-as panel — client-side format conversion via GDAL-WASM (see ./download).
import { useState } from "react";

import { type ExportFormat, exportItem, FORMATS, shapefileWarnings, type ShapefileWarnings } from "./download";
import { parquetAsset, type StacDoc } from "./stac";
import { C } from "./ui";
import { UiSelect } from "./ui/select";

const EPSG_ITEMS = [
  { value: "4326", label: "WGS 84 (EPSG:4326)" },
  { value: "26912", label: "NAD83 / UTM 12N (EPSG:26912)" },
  { value: "32612", label: "WGS84 / UTM 12N (EPSG:32612)" },
  { value: "3857", label: "Web Mercator (EPSG:3857)" },
  { value: "other", label: "Other (any EPSG)…" },
];

export function ExportPanel({ item }: { item: StacDoc }) {
  const parquet = parquetAsset(item);
  const fullBbox = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [err, setErr] = useState<string>();
  const [clipOn, setClipOn] = useState(false);
  const [bbox, setBbox] = useState<[number, number, number, number]>(fullBbox ?? [0, 0, 0, 0]);
  const [warn, setWarn] = useState<ShapefileWarnings | null>(null);  // shapefile pre-flight issues
  const [epsg, setEpsg] = useState(4326);                            // output CRS for the gdal formats
  const [customEpsg, setCustomEpsg] = useState(false);               // typed any-EPSG vs the common list
  if (!parquet) return null;

  const doExport = async (fmt: ExportFormat) => {
    setBusy(fmt);
    try {
      await exportItem(parquet.href, String(item.id ?? "export"), fmt, clipOn ? bbox : undefined, epsg);
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
        const w = await shapefileWarnings(parquet.href, clipOn ? bbox : undefined);
        if (w.any) { setWarn(w); setBusy(null); return; }
      } catch { /* check failed → just proceed to the export */ }
    }
    await doExport(fmt);
  };

  const labels = ["W", "S", "E", "N"];
  return (
    <div className="mt-3 rounded-lg border border-border bg-muted p-3">
      <div className="mb-1.5 text-xs font-semibold text-muted-foreground">Download as</div>
      <div className="flex flex-wrap items-center gap-2">
        {FORMATS.map((f) => (
          <button key={f.id} disabled={busy !== null} onClick={() => run(f.id)}
            className="rounded border border-border bg-card px-2.5 py-1 text-xs text-foreground hover:border-primary disabled:opacity-50">
            {busy === f.id ? "preparing…" : f.label}
          </button>
        ))}
        {busy && <span className={C.muted}>running in your browser · first export loads DuckDB (~a few MB)</span>}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
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
        <span>— applies to Shapefile/GeoPackage/FileGDB/FlatGeobuf and CSV; GeoJSON is always WGS 84 (spec).</span>
      </div>
      {fullBbox && (
        <div className="mt-2 text-xs">
          <label className="flex items-center gap-1.5 text-muted-foreground">
            <input type="checkbox" checked={clipOn} onChange={(e) => setClipOn(e.target.checked)} />
            Clip to area (bbox, EPSG:4326)
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
      {err && <div className="mt-1.5 text-xs text-destructive">Export failed: {err}</div>}

      {warn && (
        <div className="mt-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-xs">
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
    </div>
  );
}
