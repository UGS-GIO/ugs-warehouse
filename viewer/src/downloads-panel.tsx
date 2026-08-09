// One place to get a file. The item page used to offer the same GeoParquet four ways — an asset
// chip, an inline `Parquet ↓`, the export panel's source, and an endpoints row — so "where do I
// download this" had four answers and none of them was obviously the one.
//
// The split that matters to a reader is what they DO with it: save a file (here) versus point a
// tool at a live URL (`endpoints-panel.tsx`, "Services"). Assets that are read over HTTP rather
// than saved — PMTiles, the GL style, DuckLake — belong there, not in this list.
import { ExportPanel } from "./export-panel";
import { type Asset, type StacDoc } from "./stac";
import { C } from "./ui";

// Read by URL, not saved: these live under Services. Everything else the item publishes is a file.
const SERVICE_KEYS = new Set(["pmtiles", "style", "xyz", "ducklake", "tiles"]);

/** The item's own files. `roles:["related"]` assets (UCRC boxes/photos/attachments) are left out:
 *  the Related tables section already offers each one next to its View/Gallery buttons. */
function fileAssets(item: StacDoc): [string, Asset][] {
  return Object.entries(item.assets ?? {})
    .filter(([key, a]) => !SERVICE_KEYS.has(key) && !a.roles?.includes("related"));
}

export function DownloadsPanel({ item }: { item: StacDoc }) {
  const files = fileAssets(item);
  const hasExports = Boolean(item.assets && Object.values(item.assets).some(
    (a) => /parquet/i.test(String(a.type ?? "")) || /parquet/i.test(String(a.href ?? ""))));
  if (!files.length && !hasExports) return null;
  return (
    <section className="mt-3 rounded-lg border border-border bg-muted p-3">
      <h3 className="mb-1.5 text-sm font-semibold text-muted-foreground">Downloads</h3>
      {files.length > 0 && (
        <div className="flex flex-col gap-1">
          {files.map(([key, a]) => (
            <a key={key} href={a.href} target="_blank" rel="noopener"
              className="flex flex-wrap items-baseline gap-x-2 text-sm text-foreground no-underline hover:text-primary">
              <span className="font-medium">{a.title ?? key}</span>
              {a.type && <span className={C.muted}>{a.type.split("/").pop()}</span>}
              <span className="ml-auto text-primary">↓</span>
            </a>
          ))}
        </div>
      )}
      {/* Converted in the browser from the GeoParquet — same data, the format your tool wants. */}
      <ExportPanel item={item} embedded />
    </section>
  );
}
