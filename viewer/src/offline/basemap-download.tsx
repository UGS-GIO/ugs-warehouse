// "Save this area": download the basemap for what the map is showing, for offline use.
//
// Downloads the statewide overview (low zooms, once) plus every 7.5-minute quad the view touches,
// through the same OPFS store and service worker as data layers. Sizes come from the build's
// index.json, so the button can say what a download costs before anyone commits to it.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { qk } from "@/query-keys";
import { BASEMAP_BASE, overviewUrl, quadsInBbox, quadUrl } from "./basemap";
import * as opfs from "./opfs";
import { useStoredLayers } from "./use-offline";

// Past this many quads the view is a region, not a work area. Zoom in rather than queue a
// statewide download from a button meant for "the quads I'm in".
const MAX_QUADS = 40;

type Index = { overview: { bytes: number }; quads: Record<string, { bytes: number }> };

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
  const [done, setDone] = useState<{ n: number; of: number } | null>(null);

  const have = new Set(stored.data?.files.map((f) => f.url));
  // Only quads the build produced: a view over Nevada touches quads that do not exist.
  const quads = bbox && index.data ? quadsInBbox(bbox).filter((q) => q.code in index.data.quads) : [];
  const wanted = [
    ...(have.has(overviewUrl()) ? [] : [{ url: overviewUrl(), bytes: index.data?.overview.bytes ?? 0 }]),
    ...quads.filter((q) => !have.has(quadUrl(q.code)))
      .map((q) => ({ url: quadUrl(q.code), bytes: index.data?.quads[q.code].bytes ?? 0 })),
  ];
  const bytes = wanted.reduce((n, w) => n + w.bytes, 0);

  const save = useMutation({
    mutationFn: async () => {
      await navigator.storage?.persist?.().catch(() => false);
      if (!opfs.fitsInQuota(bytes, await opfs.quota())) {
        throw new Error(`${opfs.formatBytes(bytes)} will not fit in this browser's storage.`);
      }
      setDone({ n: 0, of: wanted.length });
      for (const [i, w] of wanted.entries()) {
        await opfs.save(w.url);
        setDone({ n: i + 1, of: wanted.length });
      }
    },
    onSettled: () => {
      setDone(null);
      // The style query re-reads what is stored, which is what points the protocol at the new files.
      client.invalidateQueries({ queryKey: qk.offlineLayers });
      client.invalidateQueries({ queryKey: ["basemap-style"] });
    },
  });

  if (!opfs.isSupported() || index.isError || !index.data) return null;

  const cls = "rounded bg-card/95 px-2 py-1 shadow hover:bg-hover disabled:opacity-60";

  if (done) return <span className={cls}>Saving basemap {done.n}/{done.of}</span>;
  if (!quads.length) return null;   // view is outside Utah
  if (quads.length > MAX_QUADS) {
    return <span className={cls} title="Zoom in to save the basemap for a work area">Zoom in to save basemap</span>;
  }
  if (!wanted.length) {
    return <span className={cls} title={`${quads.length} quad(s) in view are saved`}>✓ Basemap saved</span>;
  }
  return (
    <button type="button" className={cls} disabled={save.isPending}
      title={save.error ? save.error.message
        : `Save the basemap for the ${quads.length} quad(s) in view, for offline use`}
      onClick={() => save.mutate()}>
      {save.error ? "⚠" : "⭳"} Save basemap · {opfs.formatBytes(bytes)}
    </button>
  );
}
