// Offline data: everything saved to this device, what it costs, and a way to get rid of it.
//
// The per-layer download button and the map's "Save basemap" each see only their own file. This
// is the one place that sees all of it — and the only way to delete a saved basemap quad, which
// has no control of its own on the map.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useViewCtx } from "@/app";
import { qk } from "@/query-keys";
import { BasemapDownload } from "./basemap-download";
import { describe, type Described, sortDescribed } from "./describe";
import * as opfs from "./opfs";
import { useStoredLayers } from "./use-offline";

const BTN = "rounded border border-border px-2 py-0.5 text-sm hover:bg-hover disabled:opacity-50";

// Not cached forever: the first download asks for persistent storage, so this can flip from
// "not protected" to "protected" mid-session, and a stale answer would hide that it worked.
function usePersisted() {
  return useQuery({
    queryKey: ["storage-persisted"],
    queryFn: async () => (await navigator.storage?.persisted?.()) ?? false,
  });
}

export function OfflineManager() {
  const { allItems, openItem } = useViewCtx();
  const stored = useStoredLayers();
  const persisted = usePersisted();
  const client = useQueryClient();

  const refresh = () => {
    client.invalidateQueries({ queryKey: qk.offlineLayers });
    client.invalidateQueries({ queryKey: ["storage-persisted"] });
    // The basemap protocol reads its stored set from the style query; stale, it would keep
    // routing a deleted quad's tiles to a file that is no longer there.
    client.invalidateQueries({ queryKey: ["basemap-style"] });
  };

  const remove = useMutation({
    mutationFn: (urls: string[]) => Promise.all(urls.map((u) => opfs.remove(u))),
    onSettled: refresh,
  });
  const update = useMutation({
    mutationFn: (url: string) => opfs.save(url),
    onSettled: refresh,
  });

  if (!opfs.isSupported()) {
    return <Page><p>This browser cannot store data for offline use.</p></Page>;
  }

  const rows = sortDescribed((stored.data?.files ?? []).map((f) => describe(f, allItems)));
  const layers = rows.filter((r) => r.kind === "layer");
  const basemap = rows.filter((r) => r.kind === "basemap");
  const used = stored.data?.bytes ?? 0;
  const { quota } = stored.data?.space ?? {};
  const busy = remove.isPending || update.isPending;

  return (
    <Page>
      <section className="flex flex-col gap-1 rounded-md border border-border bg-card p-3">
        <div className="flex items-baseline justify-between">
          <span><strong>{opfs.formatBytes(used)}</strong> saved on this device</span>
          {quota ? <span className="text-sm text-muted-foreground">of {opfs.formatBytes(quota)} this browser allows</span> : null}
        </div>
        {quota ? (
          <div className="h-1.5 overflow-hidden rounded bg-muted" role="progressbar"
            aria-valuenow={used} aria-valuemin={0} aria-valuemax={quota}>
            <div className="h-full bg-primary" style={{ width: `${Math.max(1, Math.min(100, (used / quota) * 100))}%` }} />
          </div>
        ) : null}
        <p className="text-sm text-muted-foreground">
          {persisted.data
            ? "Protected: the browser will not clear this on its own."
            : "Not protected: the browser may clear this if the device runs low on space."}
        </p>
      </section>

      {/* No map view here, so only the statewide save shows; "This area" lives in the Map's panel. */}
      <BasemapDownload bbox={null} />

      {!rows.length && (
        <p className="text-muted-foreground">
          Nothing saved yet. Save the basemap above, or on the Map use the download button beside a
          layer under "On the map", to keep it for use with no connection.
        </p>
      )}

      {layers.length > 0 && (
        <Group title="Layers" rows={layers} busy={busy}
          onDelete={(r) => remove.mutate([r.url])} onUpdate={(r) => update.mutate(r.url)}
          onOpen={(r) => r.itemHref && openItem(r.itemHref)}
          onDeleteAll={() => remove.mutate(layers.map((r) => r.url))} />
      )}
      {basemap.length > 0 && (
        <Group title="Basemap" rows={basemap} busy={busy}
          onDelete={(r) => remove.mutate([r.url])} onUpdate={(r) => update.mutate(r.url)}
          onDeleteAll={() => remove.mutate(basemap.map((r) => r.url))} />
      )}

      {(remove.error || update.error) && (
        <p className="text-destructive">{String((remove.error ?? update.error)?.message)}</p>
      )}
    </Page>
  );
}

function Page({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-4">
      <h1 className="text-xl font-semibold">Offline data</h1>
      {children}
    </div>
  );
}

function Group({ title, rows, busy, onDelete, onUpdate, onOpen, onDeleteAll }: {
  title: string; rows: Described[]; busy: boolean;
  onDelete: (r: Described) => void; onUpdate: (r: Described) => void;
  onOpen?: (r: Described) => void; onDeleteAll: () => void;
}) {
  const total = rows.reduce((n, r) => n + r.bytes, 0);
  return (
    <section className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          {title} · {rows.length} · {opfs.formatBytes(total)}
        </h2>
        <button type="button" className={`${BTN} ml-auto`} disabled={busy} onClick={onDeleteAll}>
          Delete all
        </button>
      </div>
      <ul className="divide-y divide-border rounded-md border border-border">
        {rows.map((r) => (
          <li key={r.url} className="flex items-center gap-2 px-3 py-2">
            <div className="min-w-0 flex-1">
              {onOpen && r.itemHref
                ? <button type="button" className="truncate text-left hover:underline" onClick={() => onOpen(r)}>{r.label}</button>
                : <span className="truncate">{r.label}</span>}
              <div className="text-sm text-muted-foreground">
                {opfs.formatBytes(r.bytes)} · saved {new Date(r.savedAt).toLocaleDateString()}
                {r.stale && <span className="ml-2 font-medium text-primary">Newer version available</span>}
              </div>
            </div>
            {r.stale && (
              <button type="button" className={BTN} disabled={busy} onClick={() => onUpdate(r)}>Update</button>
            )}
            <button type="button" className={BTN} disabled={busy} onClick={() => onDelete(r)}
              aria-label={`Delete ${r.label}`}>Delete</button>
          </li>
        ))}
      </ul>
    </section>
  );
}
