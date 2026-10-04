// "Save offline" on a layer's page: the map, the data table or both.
import { Popover } from "@base-ui/react/popover";
import { type ReactNode, useState } from "react";

import { idOf, useViewCtx } from "@/app";
import { DownloadIcon } from "@/catalog/stac-url-chip";
import { defaultStyleUrl, parquetAsset, pmtilesLink, type StacDoc } from "@/stac";

import { describe } from "./describe";
import { formatBytes, isSupported } from "./opfs";
import * as queue from "./queue";
import { jobProgress } from "./queue";
import { useOffline } from "./store";
import { Saved, useFileSize, useTableOffline } from "./table-offline";
import { useOfflineLayer } from "./use-offline";

// A table this big starts unticked: it can be many times the map, and a phone fills up quietly.
const BIG_TABLE = 200_000_000;
const ROW_BTN = "inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 text-xs text-foreground hover:border-primary";
// One part: a checkbox before it is saved, its progress while saving, the saved copy after.
function Part({ label, hint, bytes, checked, onChecked, status }: {
  label: string; hint: string; bytes: number; checked: boolean; onChecked: (on: boolean) => void; status?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-2">
      <label className="flex cursor-pointer items-start gap-2">
        {!status && (
          <input type="checkbox" checked={checked} onChange={(e) => onChecked(e.target.checked)}
            className="mt-0.5 h-4 w-4 shrink-0 accent-primary pointer-coarse:h-5 pointer-coarse:w-5" />
        )}
        <span>{label}<span className="block text-xs text-muted-foreground">{hint}</span></span>
      </label>
      {/* role=status: the change from size to Saving to Saved is read out, not only shown. */}
      <span role="status" className="shrink-0 text-xs text-muted-foreground">{status ?? (bytes ? formatBytes(bytes) : "")}</span>
    </div>
  );
}

function Panel({ item, tiles, data, title }: { item: StacDoc; tiles?: string; data?: string; title: string }) {
  const c = useViewCtx();
  const { files } = useOffline();
  const map = useOfflineLayer(tiles, title);
  const mapFile = tiles ? files.find((f) => f.url === tiles) : undefined;
  const mapSize = useFileSize(tiles);
  const table = useTableOffline(data ?? "", title);
  const [wantMap, setWantMap] = useState(true);
  const [wantTable, setWantTable] = useState<boolean | null>(null);   // null: the default, by size
  // Ticked while its size loads, so the box does not flick on once the size arrives.
  const tableOn = wantTable ?? (table.isLoading || (table.total > 0 && table.total <= BIG_TABLE));

  const mapStatus = !tiles ? null : mapFile
    ? <Saved date={mapFile.savedAt} bytes={mapFile.bytes} what={`${title} map`} onRemove={() => map.remove.mutate()}
        extra={describe(mapFile, [{ href: "", data: item }]).stale && (
          <button type="button" className={ROW_BTN}
            onClick={() => void queue.enqueue([{ kind: "file", url: tiles, label: title, bytes: mapFile.bytes }])}>Update</button>
        )} />
    : map.progress ? `Saving ${map.progress.total ? `${Math.round((map.progress.written / map.progress.total) * 100)}%` : formatBytes(map.progress.written)}`
    : map.job?.state === "queued" ? "Queued in Downloads" : undefined;
  const tableStatus = !data ? null : table.file
    ? <Saved date={table.file.savedAt} bytes={table.file.bytes} what={`${title} table`} onRemove={() => void table.remove()} />
    : table.job && table.job.state !== "failed"
      ? (table.job.state === "running" ? `Saving ${jobProgress(table.job)}` : "Queued in Downloads") : undefined;

  const mapPending = Boolean(tiles) && !mapStatus && wantMap;
  const tablePending = Boolean(data) && !tableStatus && tableOn;
  const total = (mapPending ? mapSize.data ?? 0 : 0) + (tablePending ? table.total : 0);
  const error = map.download.error?.message ?? table.save.error?.message;

  const save = () => {
    const id = c.itemUrl ? idOf(c.itemUrl) : undefined;
    if (mapPending) {
      map.download.mutate();
      if (id && c.isLayerId(id) && !c.isActive(id)) c.addLayer(id);
      // The service worker keeps the style as it passes, so the saved layer keeps its colors offline.
      const style = defaultStyleUrl(item);
      if (style) void fetch(style).catch(() => undefined);
    }
    if (tablePending) table.save.mutate();
  };

  return (
    <>
      {tiles && mapStatus !== null && (
        <Part label="Map" hint="Draws the layer; click a feature for its details" bytes={mapSize.data ?? 0}
          checked={wantMap} onChecked={setWantMap} status={mapStatus} />
      )}
      {data && tableStatus !== null && (
        <Part label="Data table" hint={`Browse and search every feature${table.needsEngine ? " (includes the table engine, once)" : ""}`}
          bytes={table.total} checked={tableOn} onChecked={setWantTable} status={tableStatus} />
      )}
      {(mapPending || tablePending || (!mapStatus && !tableStatus)) && (
        <button type="button" onClick={save} disabled={!mapPending && !tablePending}
          className="inline-flex w-full items-center justify-center gap-1 rounded bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50">
          <DownloadIcon /> Save{total ? ` · ${formatBytes(total)}` : ""}
        </button>
      )}
      {error && <p className="text-xs text-destructive">{error}</p>}
      <p className="text-xs text-muted-foreground">Saved parts work without a connection, here and on the Map page.</p>
    </>
  );
}

export function SaveLayerOffline({ item, className }: { item: StacDoc; className: string }) {
  const tiles = pmtilesLink(item)?.href;
  const data = parquetAsset(item)?.href;
  if (!isSupported() || (!tiles && !data)) return null;
  const title = String(item.properties?.title ?? item.id ?? "");
  return (
    <Popover.Root>
      <Popover.Trigger className={className}>Save offline ▾</Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Popover.Popup className="w-80 space-y-3 rounded-md border border-border bg-card p-3 text-sm text-foreground shadow-lg outline-none">
            <Popover.Title className="text-sm font-semibold">Save offline</Popover.Title>
            <Panel item={item} tiles={tiles} data={data} title={title} />
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
