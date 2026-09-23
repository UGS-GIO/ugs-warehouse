// Save the basemap for offline use: the whole state, or just the area in view.
//
// The whole state (~180 MB) is the main option — it is what someone preparing for a trip on office
// Wi-Fi wants, and it cannot run out under them the way a saved area does at its edge. "This area"
// (the overview plus the 7.5-minute quads in view, usually a few MB) is for a phone short on space
// or a download over a field connection. Sizes come from the build's index.json, so each button
// says what it costs before anyone commits to it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CheckIcon, DownloadIcon } from "@/catalog/stac-url-chip";
import { qk } from "@/query-keys";
import { BASEMAP_BASE, overviewUrl, quadsInBbox, quadUrl, redundantWithState, stateUrl } from "./basemap";
import * as opfs from "./opfs";
import { useStoredLayers } from "./use-offline";

// Past this many quads the view is a region, not a work area; the statewide save covers that.
const MAX_QUADS = 40;

type Index = { state?: { bytes: number }; overview: { bytes: number }; quads: Record<string, { bytes: number }> };
type Part = { url: string; bytes: number };

export function useBasemapIndex() {
  return useQuery({
    queryKey: qk.basemapIndex,
    queryFn: async (): Promise<Index> => {
      const r = await fetch(`${BASEMAP_BASE}index.json`);
      if (!r.ok) throw new Error(`basemap index: ${r.status}`);
      const d = await r.json();
      // Checked here, once, because the render reads it without guards: a malformed index must hide
      // the buttons, not throw during render and take the whole map view down with it.
      if (typeof d?.overview?.bytes !== "number" || typeof d?.quads !== "object" || d.quads === null) {
        throw new Error("basemap index is malformed");
      }
      return d;
    },
    staleTime: 60 * 60 * 1000,
    retry: false,
  });
}

export function BasemapDownload({ bbox, onSaveArea }: {
  bbox: [number, number, number, number] | null;
  // Given, "This area" opens the what's-here picker (basemap + layers + maps for the view) rather
  // than saving just the basemap quads.
  onSaveArea?: () => void;
}) {
  const index = useBasemapIndex();
  const stored = useStoredLayers();
  const client = useQueryClient();
  const [progress, setProgress] = useState<{ n: number; of: number } | null>(null);

  const have = new Set(stored.data?.files.map((f) => f.url));
  const quads = bbox && index.data ? quadsInBbox(bbox).filter((q) => q.code in index.data.quads) : [];
  const area: Part[] = [
    ...(have.has(overviewUrl()) ? [] : [{ url: overviewUrl(), bytes: index.data?.overview.bytes ?? 0 }]),
    ...quads.filter((q) => !have.has(quadUrl(q.code)))
      .map((q) => ({ url: quadUrl(q.code), bytes: index.data?.quads[q.code].bytes ?? 0 })),
  ];
  const sum = (ps: Part[]) => ps.reduce((n, p) => n + p.bytes, 0);

  const save = useMutation({
    mutationFn: async ({ parts, whole }: { parts: Part[]; whole: boolean }) => {
      await navigator.storage?.persist?.().catch(() => false);
      if (!opfs.fitsInQuota(sum(parts), await opfs.quota())) {
        throw new Error(`${opfs.formatBytes(sum(parts))} will not fit in this browser's storage.`);
      }
      setProgress({ n: 0, of: parts.length });
      for (const [i, p] of parts.entries()) {
        await opfs.save(p.url);
        setProgress({ n: i + 1, of: parts.length });
      }
      // Only once the statewide file is safely on disk: it holds everything the overview and quads
      // did, so they are now duplicates. Doing this first would leave a failed download with neither.
      if (whole) for (const u of redundantWithState(have)) await opfs.remove(u);
    },
    onSettled: () => {
      setProgress(null);
      // The style query re-reads what is stored, which is what points the protocol at the files.
      client.invalidateQueries({ queryKey: qk.offlineLayers });
      client.invalidateQueries({ queryKey: ["basemap-style"] });
    },
  });

  const btn = "inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 text-xs hover:bg-hover disabled:opacity-60";
  const busy = save.isPending;

  // A panel section, not map chrome: it used to float over the map and covered the controls
  // there. Same heading style as the layer panel's other sections.
  const section = (body: React.ReactNode) => (
    <section className="flex flex-col gap-1">
      <div className="px-1.5 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Offline basemap</div>
      <div className="flex flex-wrap items-center gap-1.5 px-1.5">{body}</div>
    </section>
  );

  if (!opfs.isSupported()) return null;
  if (progress) return section(<span className="text-xs">Saving basemap {progress.n}/{progress.of}…</span>);
  // Checked before the index: offline, index.json is unreachable, and "saved" is exactly what
  // someone in the field needs to see.
  // The picker saves layers and maps, not just basemap, so it stays on offer once Utah is saved.
  const pickArea = onSaveArea && quads.length > 0 && quads.length <= MAX_QUADS && (
    <button type="button" className={btn} disabled={busy} onClick={onSaveArea}
      title="Choose what to save for the area in view: basemap, layers and maps">
      <DownloadIcon /> Save this area…
    </button>
  );
  if (have.has(stateUrl())) {
    return section(<>
      <span className="inline-flex items-center gap-1 text-xs"><CheckIcon /> All of Utah saved</span>
      {pickArea}
    </>);
  }
  if (!index.data) return null;

  const whole = index.data.state && { url: stateUrl(), bytes: index.data.state.bytes };
  const areaSaved = quads.length > 0 && area.length === 0;
  const showArea = quads.length > 0 && quads.length <= MAX_QUADS;

  return section(<>
    {whole && (
      <button type="button" className={btn} disabled={busy}
        title={save.error ? save.error.message : "Save the basemap for all of Utah, for offline use"}
        onClick={() => save.mutate({ parts: [whole], whole: true })}>
        {save.error ? "⚠" : <DownloadIcon />} All of Utah · {opfs.formatBytes(whole.bytes)}
      </button>
    )}
    {showArea && (onSaveArea
      ? pickArea
      : areaSaved
        ? <span className="inline-flex items-center gap-1 text-xs" title={`${quads.length} quad(s) in view are saved`}><CheckIcon /> Area saved</span>
        : (
          <button type="button" className={btn} disabled={busy}
            title={`Save only the ${quads.length} quad(s) in view: smaller, but blank beyond them`}
            onClick={() => save.mutate({ parts: area, whole: false })}>
            <DownloadIcon /> This area · {opfs.formatBytes(sum(area))}
          </button>
        ))}
  </>);
}
