// "Save table offline" on the data table: the layer's GeoParquet, plus the table engine if this
// device does not have it yet, queued like any other save. Once both are stored, the service worker
// answers the table's range reads from the device and the table works with no connection.
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckIcon, DownloadIcon } from "@/catalog/stac-url-chip";
import { jobProgress } from "./queue";
import * as opfs from "./opfs";
import * as queue from "./queue";
import { useOffline } from "./store";
import { useJobs } from "./use-offline";

const CLASS = "inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-xs text-muted-foreground hover:border-primary pointer-coarse:min-h-11 pointer-coarse:px-3 pointer-coarse:text-sm";

export function TableOffline({ href, title }: { href: string; title: string }) {
  const { files, engineBytes } = useOffline();
  const size = useQuery({
    queryKey: ["content-length", href],
    queryFn: async () => Number((await fetch(href, { method: "HEAD" })).headers.get("content-length")) || 0,
    staleTime: Infinity,
    retry: false,
  });
  const jobs = useJobs();
  const save = useMutation({
    mutationFn: async () => {
      const bytes = size.data ?? 0;
      if (bytes && !opfs.fitsInQuota(bytes, await opfs.quota())) {
        throw new Error(`${opfs.formatBytes(bytes)} will not fit in this browser's storage.`);
      }
      await queue.enqueue([
        ...(engineBytes ? [] : [{ kind: "engine" as const, label: "Table engine" }]),
        { kind: "file" as const, url: href, label: `${title} (table)`, bytes: bytes || undefined },
      ]);
    },
  });

  if (!opfs.isSupported()) return null;
  const saved = files.some((f) => f.url === href);
  if (saved && engineBytes) {
    return <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><CheckIcon /> Saved offline</span>;
  }
  const job = jobs.find((j) => (j.kind === "file" && j.url === href) || (j.kind === "engine" && !saved));
  if (job && job.state !== "failed") {
    return <span className="text-xs text-muted-foreground">{job.state === "running" ? `Saving ${jobProgress(job)}` : "Queued in Downloads"}</span>;
  }
  const error = save.error?.message ?? job?.error;
  return (
    <button type="button" className={CLASS} disabled={save.isPending} onClick={() => save.mutate()}
      title={error ?? "Keep this table on this device, to browse and search it with no connection"}>
      {error ? "⚠" : <DownloadIcon />} Save offline{size.data ? ` · ${opfs.formatBytes(size.data)}` : ""}
    </button>
  );
}
