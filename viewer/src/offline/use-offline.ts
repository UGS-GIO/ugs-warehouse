// Query/mutation layer over the OPFS artifact store, so the UI never touches the filesystem
// directly and one invalidation keeps every download control in sync.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { qk } from "@/query-keys";
import * as opfs from "./opfs";
import * as queue from "./queue";
import type { Job } from "./queue";
import { type Bbox, loadStoredAreas } from "./area";
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
      const row = (a: { url: string; bytes: number; version?: string; bboxes: Bbox[] }) =>
        ({ url: a.url, bytes: a.bytes, version: a.version, bboxes: a.bboxes });
      return [
        ...tiles.map((t) => ({ ...row(t), kind: "tiles" as const })),
        ...cogs.map((c) => ({ ...row(c), kind: "cog" as const })),
      ];
    },
    staleTime: Infinity,
  });
}

export type StoredAreaRow = NonNullable<ReturnType<typeof useStoredAreas>["data"]>[number];

/**
 * Saved areas whose file has been republished since they were cut: one HEAD per file, compared
 * with the version the area was saved from. Only asked online, and not retried, so offline the
 * manager simply shows no update.
 */
export function useStaleAreas(rows: StoredAreaRow[]) {
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
export async function updateArea(row: StoredAreaRow, label: string): Promise<void> {
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
