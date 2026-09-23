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

function useBasemapIndex() {
  return useQuery({
    queryKey: qk.basemapIndex,
    queryFn: async (): Promise<Index> => {
      const r = await fetch(`${BASEMAP_BASE}index.json`);
      if (!r.ok) throw new Error(`basemap index: ${r.status}`);
      return r.json();
    },
    staleTime: 60 * 60 * 1000,
    retry: false,
  });
}

export function BasemapDownload({ bbox }: { bbox: [number, number, number, number] | null }) {
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

  if (!opfs.isSupported() || !index.data) return null;

  const cls = "inline-flex items-center gap-1 rounded bg-card/95 px-2 py-1 shadow hover:bg-hover disabled:opacity-60";
  const busy = save.isPending;

  if (progress) return <span className={cls}>Saving basemap {progress.n}/{progress.of}</span>;
  if (have.has(stateUrl())) {
    return <span className={cls} title="The Utah basemap is saved on this device"><CheckIcon /> Utah basemap saved</span>;
  }

  const whole = index.data.state && { url: stateUrl(), bytes: index.data.state.bytes };
  const areaSaved = quads.length > 0 && area.length === 0;
  const showArea = quads.length > 0 && quads.length <= MAX_QUADS;

  return (
    <>
      {whole && (
        <button type="button" className={cls} disabled={busy}
          title={save.error ? save.error.message : "Save the basemap for all of Utah, for offline use"}
          onClick={() => save.mutate({ parts: [whole], whole: true })}>
          {save.error ? "⚠" : <DownloadIcon />} Save Utah basemap · {opfs.formatBytes(whole.bytes)}
        </button>
      )}
      {showArea && (areaSaved
        ? <span className={cls} title={`${quads.length} quad(s) in view are saved`}><CheckIcon /> Area saved</span>
        : (
          <button type="button" className={cls} disabled={busy}
            title={`Save only the ${quads.length} quad(s) in view: smaller, but blank beyond them`}
            onClick={() => save.mutate({ parts: area, whole: false })}>
            <DownloadIcon /> This area · {opfs.formatBytes(sum(area))}
          </button>
        ))}
    </>
  );
}
