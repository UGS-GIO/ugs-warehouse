// Every way to save a file, as one grid. Assets read over HTTP instead of saved belong in
// "Services" — see `endpoints-panel.tsx`.
import { useMutation } from "@tanstack/react-query";
import { useCallback, useRef, useState, useSyncExternalStore } from "react";

import type { ShapefileWarnings } from "@/data/download";
import { currentExports, holdsOneGeomType, subscribeExport } from "@/data/download";
import { type ExportFormat, FORMATS } from "@/data/export-formats";
import { type Asset, assetKind, isParquetAsset, parquetAsset, type StacDoc } from "@/stac";
import { C } from "@/ui/ui";
import { UiSelect } from "@/ui/select";

const SERVICE_KEYS = new Set(["pmtiles", "style", "xyz", "ducklake", "tiles"]);

const EPSG_ITEMS = [
  { value: "4326", label: "WGS 84 (EPSG:4326)" },
  { value: "26912", label: "NAD83 / UTM 12N (EPSG:26912)" },
  { value: "32612", label: "WGS84 / UTM 12N (EPSG:32612)" },
  { value: "3857", label: "Web Mercator (EPSG:3857)" },
  { value: "other", label: "Other (any EPSG)…" },
];

const HINTS: Record<ExportFormat, string> = {
  shp: "ArcMap · universal",
  gpkg: "QGIS · ArcGIS Pro",
  gdb: "ArcGIS Pro",
  fgb: "streaming · web",
  geojson: "web · always WGS 84",
  csv: "spreadsheet · WKT geometry",
};

/** `…/thing.parquet?x=1` → `parquet` */
const extOf = (href: string) => href.split(/[?#]/)[0].match(/\.([a-z0-9]{1,8})$/i)?.[1].toLowerCase();

/**
 * `related` assets are skipped — the Related tables section already offers each one.
 *
 * Zarr stores are skipped too: the href is a store PREFIX, not an object, so a plain GET returns
 * the bucket's NoSuchKey XML. Nothing here can save one as a file — an Icechunk store is
 * content-addressed chunks plus manifests, with no single object holding a variable or a
 * timestep. It is read over HTTP, so per this file's own split it belongs in Services.
 */
const fileAssets = (item: StacDoc): [string, Asset][] =>
  Object.entries(item.assets ?? {})
    .filter(([key, a]) => !SERVICE_KEYS.has(key)
      && !a.roles?.includes("related")
      && assetKind(a) !== "zarr");

// Formats that pull gdal3.js (~40 MB) on first use, versus DuckDB's few MB.
const GDAL_FORMATS = new Set<ExportFormat>(["shp", "gpkg", "gdb", "fgb"]);
const FORMAT_LABEL = Object.fromEntries(FORMATS.map((f) => [f.id, f.label])) as Record<ExportFormat, string>;

const fmtBytes = (n: number) =>
  n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`;

const TILE = "flex items-start justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 " +
  "text-left text-sm text-foreground no-underline hover:border-primary hover:text-primary " +
  // aria-disabled, not :disabled — the buttons stay focusable, so the native variant never matches.
  "aria-disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:hover:border-border " +
  "aria-disabled:hover:text-foreground";
const SUB = "mt-0.5 block text-xs font-normal text-muted-foreground";
const BBOX_LABELS = ["W", "S", "E", "N"];

export function DownloadsPanel({ item }: { item: StacDoc }) {
  const parquet = parquetAsset(item);
  const fullBbox = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const [clipOn, setClipOn] = useState(false);
  const [bbox, setBbox] = useState<[number, number, number, number]>(fullBbox ?? [0, 0, 0, 0]);
  const [epsg, setEpsg] = useState(4326);
  const [customEpsg, setCustomEpsg] = useState(false);

  // The export outlives this component: the panel is keyed per item, so switching items remounts
  // it while the run continues. Reading the module's own state keeps the indicator and Cancel on
  // screen wherever the user ends up.
  const running = useSyncExternalStore(subscribeExport, currentExports);
  const invoker = useRef<HTMLButtonElement | null>(null);
  const ticket = useRef<number | null>(null);

  const run = useMutation({
    mutationFn: async ({ fmt, force }: { fmt: ExportFormat; force?: boolean }) => {
      const clip = clipOn ? bbox : undefined;
      const { beginExport, endRun, exportItem, exportWarnings, startRun } = await import("@/data/download");
      // The ticket is taken BEFORE the pre-flight, which is the slow part — a cancel during it
      // has to suppress the delivery too.
      const epoch = ticket.current = beginExport();
      // Track it from here, not from exportItem: the pre-flight is the long phase, and a panel
      // remounted during it would otherwise show no indicator and no way to cancel.
      startRun({ id: epoch, stem: String(item.id ?? "export"), fmt });
      // Every format reads the whole GeoParquet into the tab, so every format is pre-flighted.
      // A failed pre-flight just proceeds to the export.
      if (!force) {
        const w = await exportWarnings(parquet!.href, fmt, clip)
          .catch((e) => { console.warn("export pre-flight failed", e); return null; });
        if (w?.any) { endRun(epoch); return w; }   // no export follows, so release the ticket
      }
      await exportItem(parquet!.href, String(item.id ?? "export"), fmt, clip, epsg, epoch);
    },
  });
  const busy = run.isPending ? run.variables.fmt : null;
  // Suppresses the delivery, not the work: see cancelExports.
  // Cancels one run by id — never everything in flight.
  const cancelRun = (id: number) => {
    void import("@/data/download").then((m) => m.cancelExport(id));
    if (id === ticket.current) { ticket.current = null; run.reset(); }
  };
  // During the pre-flight the run has no record yet, so this panel's own ticket is the handle.
  const pending = busy && ticket.current !== null && !running.some((r) => r.id === ticket.current)
    ? ticket.current : null;
  // Stable identity: an inline arrow is a new ref every commit, so React would re-run it on each
  // render and steal focus back from the clip and CRS inputs the warning tells the user to use.
  const focusWarning = useCallback((el: HTMLDivElement | null) => { el?.focus(); }, []);
  // Dismissing puts focus back on the button that opened the warning, not on <body>.
  const dismiss = () => { run.reset(); invoker.current?.focus(); };
  const warn: ShapefileWarnings | undefined = run.data?.any ? run.data : undefined;

  const files = fileAssets(item);
  if (!files.length) return null;
  const data = files.filter(([, a]) => isParquetAsset(a));
  const sidecars = files.filter(([, a]) => !isParquetAsset(a));

  const assetTile = ([key, a]: [string, Asset]) => (
    <a key={key} href={a.href} target="_blank" rel="noopener" className={TILE}>
      <span className="font-medium">
        {a.title ?? key}
        <span className={SUB}>{extOf(a.href)}</span>
      </span>
      <span aria-hidden className="shrink-0 text-primary">↓</span>
    </a>
  );

  return (
    <section className="mt-3 rounded-lg border border-border bg-muted p-3">
      <h3 className="mb-1.5 text-sm font-semibold text-muted-foreground">Downloads</h3>
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {data.map(assetTile)}
        {parquet && FORMATS.map((f) => (
          <button key={f.id} aria-disabled={run.isPending} aria-busy={busy === f.id}
            onClick={(e) => {
              if (run.isPending) return;      // aria-disabled keeps it focusable, so guard the click
              invoker.current = e.currentTarget;
              run.mutate({ fmt: f.id });
            }}
            aria-label={`Download ${f.label}`} className={TILE}>
            <span className="font-medium">
              {f.label}
              <span className={SUB}>{HINTS[f.id]}</span>
            </span>
            <span aria-hidden className="shrink-0 text-primary">{busy === f.id ? "…" : "↓"}</span>
          </button>
        ))}
        {sidecars.map(assetTile)}
      </div>
      {/* Always mounted: a live region created with its text is announced unreliably. Announces
          the end as well as the start, since neither is otherwise visible to a screen reader.
          The warning box below carries no live role for the same reason — moving focus into a
          labelled container is what announces it. */}
      <p role="status" aria-live="polite" className={`mt-1.5 ${C.muted} ${busy ? "" : "sr-only"}`}>
        {busy
          ? `preparing in your browser · the first one loads DuckDB${GDAL_FORMATS.has(busy) ? " and GDAL (~40 MB)" : " (~a few MB)"}`
          : run.isSuccess && !warn ? "export ready" : ""}
      </p>
      <div role="alert" className={run.error ? `mt-1.5 text-sm text-destructive` : "sr-only"}>
        {run.error ? `Download failed: ${run.error.message}` : ""}
      </div>
      {pending !== null && (
        <button onClick={() => cancelRun(pending)}
          className="mt-1 block text-sm text-muted-foreground hover:underline">
          Cancel
        </button>
      )}
      {/* One per live run: an export started before the user navigated here is still theirs. */}
      {running.map((r) => (
        <button key={r.id} onClick={() => cancelRun(r.id)}
          className="mt-1 block text-sm text-muted-foreground hover:underline">
          Cancel {r.fmt} export{r.id === ticket.current ? "" : ` of ${r.stem}`}
        </button>
      ))}

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
                      {BBOX_LABELS[i]}
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

      {warn && (
        <div aria-labelledby="dl-warn-title" aria-describedby="dl-warn-why" tabIndex={-1} ref={focusWarning}
          onKeyDown={(e) => { if (e.key === "Escape") dismiss(); }}
          className="mt-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-sm">
          <div id="dl-warn-title" className="font-semibold text-amber-700 dark:text-amber-400">
            {warn.tooBigToInspect || warn.overBrowserLimit
              ? "This export is too big for the browser"
              : `${FORMAT_LABEL[run.variables!.fmt]} will mangle this data`}
          </div>
          <ul id="dl-warn-why" className="mt-1 list-disc space-y-0.5 pl-4 text-foreground">
            {warn.mixedGeometry.length > 0 && (
              <li>
                <b>Mixed geometry</b> ({warn.mixedGeometry.join(", ").toLowerCase()}) —{" "}
                {FORMAT_LABEL[run.variables!.fmt]} holds one geometry type per layer, so the
                conversion fails rather than dropping the others. Use GeoPackage or FlatGeobuf,
                which take all of them.
              </li>
            )}
            {warn.longNames.length > 0 && (
              <li><b>{warn.longNames.length} field name{warn.longNames.length === 1 ? "" : "s"} over 10 chars</b> get truncated (e.g. <code>{warn.longNames[0]}</code> → <code>{warn.longNames[0].slice(0, 10)}</code>).</li>
            )}
            {warn.collisions.length > 0 && (
              <li><b>Field-name collisions</b> after truncation — <code>{warn.collisions[0][0]}</code> &amp; <code>{warn.collisions[0][1]}</code> collapse to the same name (data loss).</li>
            )}
            {warn.tooManyFields && <li><b>{warn.fieldCount} fields</b> exceeds the 255-field shapefile limit.</li>}
            {warn.tooBigToInspect && (
              <li>
                <b>Too big to convert in the browser</b> —{" "}
                {warn.sizeUnknown
                  ? "the server won't report this file's size, so we can't tell whether it fits"
                  : `the GeoParquet is ${fmtBytes(warn.sourceBytes)}`}
                , and the export has to load all of it into the tab first. Download the GeoParquet
                above and convert it locally (QGIS, GDAL). Clipping doesn't help: the whole file is
                read either way.
              </li>
            )}
            {warn.overBrowserLimit && !warn.tooBigToInspect && (
              <li>
                <b>Too big to convert in the browser</b> — the conversion needs about{" "}
                {fmtBytes(warn.estPeakBytes)} of memory and the tab has roughly 1.5 GB. Clip to a
                smaller area, or download the GeoParquet and convert locally.
              </li>
            )}
            {warn.over2gb && (
              <li>
                <b>Over the 2 GB per-file shapefile limit</b> — estimated{" "}
                {fmtBytes(warn.estShpBytes)} of geometry (.shp) and {fmtBytes(warn.estDbfBytes)} of
                attributes (.dbf). Use GeoPackage, or clip to a smaller area.
              </li>
            )}
          </ul>
          <div className="mt-2 flex flex-wrap gap-2">
            {/* GeoPackage shares the tab and the wasm instance, so it is no way out of a memory
                ceiling — only out of the limits the single-geometry formats impose. */}
            {!warn.overBrowserLimit && holdsOneGeomType(run.variables!.fmt) && (
              <button onClick={() => run.mutate({ fmt: "gpkg" })}
                className="rounded border border-border bg-primary px-2 py-0.5 text-primary-foreground hover:opacity-90">
                Use GeoPackage instead
              </button>
            )}
            {/* Forcing past a memory ceiling crashes the tab, and past mixed geometry the
                conversion fails outright — neither produces a file. */}
            {!warn.overBrowserLimit && warn.mixedGeometry.length === 0 && (
              <button onClick={() => run.mutate({ fmt: run.variables!.fmt, force: true })}
                className="rounded border border-border bg-card px-2 py-0.5 text-foreground hover:border-primary">
                Download anyway
              </button>
            )}
            <button onClick={dismiss} className="text-muted-foreground hover:underline">Cancel</button>
          </div>
        </div>
      )}
    </section>
  );
}
