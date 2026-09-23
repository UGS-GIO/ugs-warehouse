// Per-layer "keep offline" control, shown on an active layer row.
//
// Three states, one button: not stored (download), downloading (percent, click to nothing), stored
// (size, click to delete). The stored state is the only one that needs a second affordance, so the
// title carries the delete and the glyph carries the state.
import { useStoredLayers, useOfflineLayer } from "./use-offline";
import { DownloadIcon } from "@/catalog/stac-url-chip";
import { formatBytes, isSupported } from "./opfs";

const CLASS = "inline-flex shrink-0 items-center gap-1 rounded px-1 text-[11px] tabular-nums text-muted-foreground hover:text-foreground";

export function OfflineButton({ href, title }: { href?: string; title: string }) {
  const stored = useStoredLayers();
  const { download, remove, progress } = useOfflineLayer(href);

  // Nothing to download (aspatial or zarr layer), or a browser with no OPFS: show nothing rather
  // than a control that cannot work.
  if (!href || !isSupported()) return null;

  const file = stored.data?.files.find((f) => f.url === href);
  const error = download.error ?? remove.error;

  if (progress) {
    const pct = progress.total ? Math.round((progress.written / progress.total) * 100) : null;
    return (
      <span className={CLASS} title={`Downloading ${title}`}>
        {pct === null ? formatBytes(progress.written) : `${pct}%`}
      </span>
    );
  }

  if (file) {
    return (
      <button type="button" className={`${CLASS} text-foreground`} disabled={remove.isPending}
        title={`Stored offline (${formatBytes(file.bytes)}) — click to delete`}
        onClick={() => remove.mutate()}>
        <DownloadIcon /> {formatBytes(file.bytes)}
      </button>
    );
  }

  return (
    <button type="button" className={CLASS} disabled={download.isPending}
      title={error ? String(error.message) : `Keep ${title} on this device for offline use`}
      onClick={() => download.mutate()}>
      {error ? "⚠" : <DownloadIcon />}
    </button>
  );
}
