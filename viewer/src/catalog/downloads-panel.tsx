// Every way to save a file, as one grid. Assets read over HTTP instead of saved belong in
// "Services" — see `endpoints-panel.tsx`.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { type InputHTMLAttributes, useCallback, useRef, useState, useSyncExternalStore } from "react";

import { currentExports, subscribeExport } from "@/data/download";
import { type ExportFormat, FORMATS } from "@/data/export-formats";
import { toBbox } from "@/lib/bbox";
import { usePreviewBounds } from "@/map/preview-map";
import { type Asset, assetKind, isParquetAsset, parquetAsset, type StacDoc } from "@/stac";
import { C } from "@/ui/ui";
import { UiSelect } from "@/ui/select";

import { exportFindings, type Warning } from "./export-findings";
import { ExportWarning } from "./export-warning";

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
  parquet: "clipped · always WGS 84",
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

const TILE = "flex items-start justify-between gap-2 rounded-md border border-border bg-card px-3 py-2 " +
  "text-left text-sm text-foreground no-underline hover:border-primary hover:text-primary " +
  // aria-disabled, not :disabled — the buttons stay focusable, so the native variant never matches.
  "aria-disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:hover:border-border " +
  "aria-disabled:hover:text-foreground";
const SUB = "mt-0.5 block text-xs font-normal text-muted-foreground";
const BBOX_LABELS = ["W", "S", "E", "N"];

type Clip = [number, number, number, number];

const isPreset = (epsg: number) => EPSG_ITEMS.some((o) => o.value === String(epsg));

/** Keeps its own text while typing and commits on blur or Enter. A change from outside (reset,
 *  back/forward) is written into the field unless it has focus: remounting it instead would drop
 *  focus every time Enter commits. */
function NumberField({ value, valid = Number.isFinite, onCommit, ...rest }:
  { value: number; valid?: (n: number) => boolean; onCommit: (n: number) => void }
  & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "defaultValue" | "onBlur" | "onKeyDown">) {
  // A new callback per value, so React calls it again whenever the value changes.
  const sync = useCallback((el: HTMLInputElement | null) => {
    if (el && el !== document.activeElement) el.value = String(value);
  }, [value]);
  const commit = (el: HTMLInputElement) => {
    const n = Number(el.value);
    if (!el.value.trim() || !valid(n)) el.value = String(value);
    else if (n !== value) onCommit(n);
  };
  return (
    <input ref={sync} type="number" defaultValue={value} {...rest}
      onBlur={(e) => commit(e.currentTarget)}
      onKeyDown={(e) => { if (e.key === "Enter") commit(e.currentTarget); }}
      className="w-24 rounded border border-input bg-card px-1.5 py-0.5 text-foreground" />
  );
}

export function DownloadsPanel({ item }: { item: StacDoc }) {
  const parquet = parquetAsset(item);
  const fullBbox = toBbox(item.bbox);
  // CRS and clip live in the URL, so a reload or a shared link keeps them.
  const { crs: epsg = 4326, clip } = useSearch({ from: "__root__" });
  const navigate = useNavigate();
  const setSearch = (next: { crs?: number; clip?: Clip }) =>
    void navigate({
      to: ".", replace: true,
      search: (prev) => ({
        ...prev,
        ...("crs" in next && { crs: next.crs === 4326 ? undefined : next.crs }),
        ...("clip" in next && { clip: next.clip }),
      }),
    });
  const view = usePreviewBounds();
  const [pickedOther, setPickedOther] = useState(false);
  const customEpsg = pickedOther || !isPreset(epsg);
  const queryClient = useQueryClient();

  // The export outlives this component: the panel is keyed per item, so switching items remounts
  // it while the run continues. Reading the module's own state keeps the indicator and Cancel on
  // screen wherever the user ends up.
  const running = useSyncExternalStore(subscribeExport, currentExports);
  const invoker = useRef<HTMLButtonElement | null>(null);
  const ticket = useRef<number | null>(null);

  const run = useMutation({
    mutationFn: async ({ fmt, force }: { fmt: ExportFormat; force?: boolean }): Promise<Warning | undefined> => {
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
        // Cached per file, format and clip: a second click on the same export skips the read.
        const w = await queryClient.fetchQuery({
          queryKey: ["export-preflight", parquet!.href, fmt, clip ?? null],
          queryFn: () => exportWarnings(parquet!.href, fmt, clip),
          staleTime: Infinity,
        }).catch((e) => { console.warn("export pre-flight failed", e); return null; });
        const found = w ? exportFindings(w, fmt) : [];
        if (found.length) { endRun(epoch); return { fmt, findings: found }; }   // no export follows, so release the ticket
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
  // Dismissing puts focus back on the button that opened the warning, not on <body>.
  const dismiss = () => { run.reset(); invoker.current?.focus(); };
  const warn = run.data;

  const files = fileAssets(item);
  if (!files.length) return null;
  const data = files.filter(([, a]) => isParquetAsset(a));
  const sidecars = files.filter(([, a]) => !isParquetAsset(a));

  const formatTile = (fmt: ExportFormat, label: string) => (
    <button key={fmt} aria-disabled={run.isPending} aria-busy={busy === fmt}
      onClick={(e) => {
        if (run.isPending) return;      // aria-disabled keeps it focusable, so guard the click
        invoker.current = e.currentTarget;
        run.mutate({ fmt });
      }}
      aria-label={`Download ${label}`} className={TILE}>
      <span className="font-medium">
        {label}
        <span className={SUB}>{HINTS[fmt]}</span>
      </span>
      <span aria-hidden className="shrink-0 text-primary">{busy === fmt ? "…" : "↓"}</span>
    </button>
  );

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
        {/* The archive is the whole file; under a clip, the same tile exports just the clipped rows. */}
        {data.map((entry) => (clip && entry[1] === parquet
          ? formatTile("parquet", entry[1].title ?? "GeoParquet")
          : assetTile(entry)))}
        {parquet && FORMATS.map((f) => formatTile(f.id, f.label))}
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
      {/* One per live run, registered from the ticket, so this covers the pre-flight phase too.
          An export started before the user navigated here is still theirs. */}
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
                  if (v === "other") setPickedOther(true);
                  else { setPickedOther(false); setSearch({ crs: Number(v) }); }
                }}
                items={EPSG_ITEMS} />
            </label>
            {customEpsg && (
              <label className="flex items-center gap-1">
                EPSG:
                <NumberField value={epsg} min={1024} max={999999} autoFocus
                  valid={(n) => Number.isInteger(n) && n >= 1024 && n <= 999999}
                  onCommit={(crs) => setSearch({ crs })} />
              </label>
            )}
          </div>
          {fullBbox && (
            <div className="mt-2 text-sm">
              <label className="flex items-center gap-1.5 text-muted-foreground">
                <input type="checkbox" checked={!!clip}
                  onChange={(e) => setSearch({ clip: e.target.checked ? fullBbox : undefined })} />
                Clip to an area (bbox, EPSG:4326)
              </label>
              {view && (
                <button onClick={() => setSearch({ clip: view })} className="mt-1 text-primary">
                  Use map view
                </button>
              )}
              {clip && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {clip.map((v, i) => (
                    <label key={i} className="flex items-center gap-1 text-muted-foreground">
                      {BBOX_LABELS[i]}
                      <NumberField value={v} step="0.01" onCommit={(n) => {
                        const next: Clip = [...clip];
                        next[i] = n;
                        setSearch({ clip: next });
                      }} />
                    </label>
                  ))}
                  <button onClick={() => setSearch({ clip: fullBbox })} className="text-primary">reset</button>
                </div>
              )}
            </div>
          )}
        </details>
      )}

      {warn && (
        <ExportWarning warn={warn} onDismiss={dismiss}
          onUseGeoPackage={() => run.mutate({ fmt: "gpkg" })}
          onForce={() => run.mutate({ fmt: warn.fmt, force: true })} />
      )}
    </section>
  );
}
