// Query/mutation layer over the OPFS artifact store, so the UI never touches the filesystem
// directly and one invalidation keeps every download control in sync.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { qk } from "@/query-keys";
import * as opfs from "./opfs";
import * as queue from "./queue";
import type { Job } from "./queue";
import { loadStoredAreas } from "./area";
import { listCogAreas } from "./cog-area";

/** What is stored, plus the browser's storage headroom. One query so the UI reads one status. */
export function useStoredLayers() {
  return useQuery({
    queryKey: qk.offlineLayers,
    queryFn: async () => {
      const [files, space] = await Promise.all([opfs.list(), opfs.quota()]);
      return { files, space, bytes: files.reduce((n, f) => n + f.bytes, 0) };
    },
    staleTime: Infinity,   // only this module's mutations change it
  });
}

/** Progress of an in-flight download: bytes written, and the total when the server declared one. */
export type Progress = { written: number; total?: number };

/** Every save waiting, running or failed, from the device's download queue (offline/queue.ts). */
export const useJobs = (): Job[] => useSyncExternalStore(queue.subscribe, queue.snapshot, queue.snapshot);

/**
 * Queue one artifact for download, or delete the stored copy, keeping the pmtiles protocol's view
 * in step. `progress` is read from the queue, so it survives leaving the page and coming back.
 */
export function useOfflineLayer(href: string | undefined, label = href ?? "") {
  const client = useQueryClient();
  const job = useJobs().find((j) => j.kind === "file" && j.url === href);
  const progress: Progress | null = job?.state === "running"
    ? { written: job.done ?? 0, total: job.total }
    : null;

  const download = useMutation({
    mutationFn: async () => {
      if (!href) throw new Error("This layer has no downloadable file.");
      const space = await opfs.quota();
      const head = await fetch(href, { method: "HEAD" }).catch(() => null);
      const size = Number(head?.headers.get("content-length")) || 0;
      if (size && !opfs.fitsInQuota(size, space)) {
        throw new Error(`${opfs.formatBytes(size)} will not fit in the ${opfs.formatBytes(
          (space.quota ?? 0) - (space.usage ?? 0))} this browser still allows.`);
      }
      await queue.enqueue([{ kind: "file", url: href, label, bytes: size || undefined }]);
    },
  });

  const remove = useMutation({
    mutationFn: async () => {
      if (href) await opfs.remove(href);
    },
    onSettled: () => client.invalidateQueries({ queryKey: qk.offlineLayers }),
  });

  return { download, remove, progress, job };
}

/**
 * Layers and plates saved by area rather than whole (offline/area.ts, offline/cog-area.ts): what
 * the "Save this area" picker produces. Listed apart from whole files because they delete apart.
 */
export function useStoredAreas() {
  return useQuery({
    queryKey: ["offline-areas"],
    queryFn: async () => {
      const [tiles, cogs] = await Promise.all([loadStoredAreas(), listCogAreas()]);
      return [
        ...tiles.map((t) => ({ url: t.url, bytes: t.bytes, kind: "tiles" as const })),
        ...cogs.map((c) => ({ url: c.url, bytes: c.bytes, kind: "cog" as const })),
      ];
    },
    staleTime: Infinity,
  });
}
