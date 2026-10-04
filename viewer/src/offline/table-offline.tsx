// "Save table offline" on the data table: the layer's GeoParquet, plus the table engine if this
// device does not have it yet, queued like any other save. Once both are stored, the service worker
// answers the table's range reads from the device and the table works with no connection.
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { CheckIcon, DownloadIcon, TrashIcon } from "@/catalog/stac-url-chip";
import { ENGINE_BYTES } from "./engine";
import { jobProgress } from "./queue";
import * as opfs from "./opfs";
import * as queue from "./queue";
import { removeFiles, useOffline } from "./store";
import { useJobs } from "./use-offline";

const CLASS = "inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-xs text-muted-foreground hover:border-primary pointer-coarse:min-h-11 pointer-coarse:px-3 pointer-coarse:text-sm";

/** A file's size from a HEAD request, asked once per URL. */
export const useFileSize = (href: string | undefined) => useQuery({
  queryKey: ["content-length", href],
  queryFn: async () => {
    const res = await fetch(href ?? "", { method: "HEAD" });
    // An error page has a size too, and it is not the file's.
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return Number(res.headers.get("content-length")) || 0;
  },
  enabled: Boolean(href),
  staleTime: Infinity,
  retry: false,
});

/** One layer's table, offline: its size (plus the engine the first time), its saved copy, its job. */
export function useTableOffline(href: string, title: string) {
  const { files, engineBytes } = useOffline();
  const size = useFileSize(href);
  const jobs = useJobs();
  const file = files.find((f) => f.url === href);
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
  // The first table saved also brings the table engine, so the size counts it too.
  const total = size.data ? size.data + (engineBytes ? 0 : ENGINE_BYTES) : 0;
  const job = jobs.find((j) => (j.kind === "file" && j.url === href) || (j.kind === "engine" && !file));
  return { file: engineBytes ? file : undefined, job, save, total, needsEngine: !engineBytes,
           isLoading: size.isLoading, remove: () => removeFiles([href]) };
}

// A saved copy: its date and size, and a button that removes it. 44 px on a touch screen.
export function Saved({ date, bytes, what, onRemove, extra }: {
  date: number; bytes: number; what: string; onRemove: () => void; extra?: ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1"><CheckIcon /> Saved {new Date(date).toLocaleDateString()} · {opfs.formatBytes(bytes)}</span>
      {extra}
      <button type="button" onClick={onRemove} title={`Remove the saved ${what}`} aria-label={`Remove the saved ${what}`}
        className="inline-flex items-center justify-center rounded p-1 hover:bg-muted hover:text-destructive pointer-coarse:min-h-11 pointer-coarse:min-w-11">
        <TrashIcon />
      </button>
    </span>
  );
}

export function TableOffline({ href, title }: { href: string; title: string }) {
  const { file, job, save, total, needsEngine, remove } = useTableOffline(href, title);
  if (!opfs.isSupported()) return null;
  if (file) {
    // The table engine stays: other saved tables use it, and the Offline data page removes it.
    return <Saved date={file.savedAt} bytes={file.bytes} what={`${title} table`} onRemove={() => void remove()} />;
  }
  if (job && job.state !== "failed") {
    return <span className="text-xs text-muted-foreground">{job.state === "running" ? `Saving ${jobProgress(job)}` : "Queued in Downloads"}</span>;
  }
  const error = save.error?.message ?? job?.error;
  return (
    <button type="button" className={CLASS} disabled={save.isPending} onClick={() => save.mutate()}
      title={error ?? (needsEngine
        ? `Keep this table on this device. The first table saved also brings the table engine (${opfs.formatBytes(ENGINE_BYTES)}).`
        : "Keep this table on this device, to browse and search it with no connection")}>
      {error ? "⚠" : <DownloadIcon />} Save offline{total ? ` · ${opfs.formatBytes(total)}` : ""}
    </button>
  );
}
