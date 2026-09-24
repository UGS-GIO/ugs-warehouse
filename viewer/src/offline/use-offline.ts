// Hooks for the download controls: the queue (offline/queue.ts) for what is saving, the store
// (offline/store.ts) for what is saved, and TanStack Query only for questions asked of the network.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { BASEMAP_BASE } from "./basemap";
import { useBasemapIndex } from "./basemap-download";
import * as opfs from "./opfs";
import * as queue from "./queue";
import type { Job } from "./queue";
import * as store from "./store";
import type { StoredArea } from "./store";

/** Progress of an in-flight download: bytes written, and the total when the server declared one. */
export type Progress = { written: number; total?: number };

/** Every save waiting, running or failed, from the device's download queue (offline/queue.ts). */
export const useJobs = (): Job[] => useSyncExternalStore(queue.subscribe, queue.snapshot, queue.snapshot);

/**
 * Queue one artifact for download, or delete the stored copy, keeping the pmtiles protocol's view
 * in step. `progress` is read from the queue, so it survives leaving the page and coming back.
 */
export function useOfflineLayer(href: string | undefined, label = href ?? "") {
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
    mutationFn: () => store.removeFiles(href ? [href] : []),
  });

  return { download, remove, progress, job };
}

/**
 * Saved areas whose file has been republished since they were cut: one HEAD per file, compared
 * with the version the area was saved from. Only asked online, and not retried, so offline the
 * manager simply shows no update.
 */
export function useStaleAreas(rows: StoredArea[]) {
  return useQuery({
    queryKey: ["offline-areas", "stale", rows.map((r) => `${r.url}@${r.version}`).join("|")],
    queryFn: async () => {
      const { currentVersion } = await import("./opfs-name");
      const stale = new Set<string>();
      await Promise.all(rows.filter((r) => r.version).map(async (r) => {
        const now = await currentVersion(r.url).catch(() => undefined);
        if (now && now !== r.version) stale.add(r.url);
      }));
      return stale;
    },
    enabled: rows.length > 0,
    retry: false,
    staleTime: 10 * 60_000,
  });
}

/** Re-cut a saved area against the file's current version, one queued job per area saved. */
export async function updateArea(row: StoredArea, label: string): Promise<void> {
  const jobs: queue.JobSpec[] = [];
  for (const bbox of row.bboxes) {
    if (row.kind === "tiles") {
      const plan = await (await import("./area")).planArea(row.url, bbox);
      jobs.push({ kind: "area", plan, label, bytes: plan.bytes });
    } else if (/\.parquet$/i.test(row.url)) {
      const plan = await (await import("./table-area")).planTableArea(row.url, bbox);
      jobs.push({ kind: "table", plan, label, bytes: plan.bytes });
    } else {
      const plan = await (await import("./cog-area")).planCogArea(row.url, bbox);
      jobs.push({ kind: "cog", plan, label, bytes: plan.bytes });
    }
  }
  await queue.enqueue(jobs);
}

/**
 * Saved basemap files older than the published build, which Offline data offers to update. The
 * index's build time answers for all of them in the one fetch the page already makes; a build
 * from before the index carried it is asked file by file (its Last-Modified). Online only.
 */
export function useStaleBasemaps(files: opfs.StoredFile[]) {
  const { data: index } = useBasemapIndex();
  const saved = files.filter((f) => f.url.startsWith(BASEMAP_BASE));
  return useQuery({
    queryKey: ["basemap-stale", index?.built ?? "unknown", saved.map((f) => `${f.url}@${f.savedAt}`).join("|")],
    queryFn: async () => {
      const built = index?.built;
      const { publishedAt } = await import("./opfs-name");
      const stale = new Set<string>();
      await Promise.all(saved.map(async (f) => {
        const at = built ?? await publishedAt(f.url).catch(() => undefined);
        if (at !== undefined && f.savedAt < at) stale.add(f.url);
      }));
      return stale;
    },
    enabled: saved.length > 0 && index !== undefined,
    retry: false,
    staleTime: 10 * 60_000,
  });
}
