// API & data endpoints for an item: PMTiles, XYZ, GL style, ArcGIS VectorTileServer.
import { useEffect, useRef, useState } from "react";

import { ducklakeAsset, esriVectorTileUrl, parquetAsset, featuresCollectionUrl, pmtilesLink, rendersOf, type StacDoc,
  tilesStyleUrl, xyzTilesUrl } from "./stac";
import { usePreviewMap } from "./preview-map";

function CopyBtn({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);  // clear a pending reset on unmount
  return (
    <button
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setDone(false), 1200);
      }}
      className="rounded border border-border bg-card px-1.5 py-0.5 text-[11px] text-foreground hover:border-primary">
      {done ? "copied" : "copy"}
    </button>
  );
}

export function EndpointsPanel({ item }: { item: StacDoc }) {
  const id = String(item.id ?? "");
  const coll = featuresCollectionUrl(id);
  const pq = parquetAsset(item);
  const pm = pmtilesLink(item);
  const ducklake = ducklakeAsset(item);
  const esriRenders = Object.keys(rendersOf(item)).sort();
  // Follow the map's "Symbolize by" picker, so the URL you copy is the symbology on screen.
  const { render: shown } = usePreviewMap();
  const [pickedRender, setPickedRender] = useState<string>();
  // Explicit choice wins; otherwise track the map so the two never disagree silently.
  const chosen = [pickedRender, shown].find((r) => r && esriRenders.includes(r)) ?? esriRenders[0];
  const rows: { label: string; desc: string; url: string; pick?: React.ReactNode; unavailable?: string }[] = [];
  if (coll) {
    rows.push({ label: "OGC API Features", desc: "REST feature service — collection metadata", url: coll });
    rows.push({ label: "Features (GeoJSON)", desc: "Query features as GeoJSON (paged)", url: `${coll}/items?limit=50` });
  }
  if (pq) rows.push({ label: "GeoParquet", desc: "Columnar file — DuckDB / GeoPandas / QGIS", url: pq.href });
  // PMTiles is the generic answer, not one option among equals: MapLibre, Leaflet, OpenLayers and
  // recent QGIS read it straight off the CDN with range requests — no service in the path.
  if (pm) rows.push({ label: "PMTiles", desc: "Vector tiles — MapLibre, Leaflet, OpenLayers, QGIS. Read direct from the CDN", url: pm.href });
  // The tiles service exists for clients that cannot read PMTiles directly. Only offered when the
  // item actually has PMTiles, since that archive is what it serves.
  if (pm) {
    const xyz = xyzTilesUrl(id);
    if (xyz) rows.push({ label: "XYZ tiles (fallback)", desc: "For clients that cannot read PMTiles. Prefer PMTiles above", url: xyz });
    const style = tilesStyleUrl(id, chosen);
    if (style) rows.push({ label: "Vector tile style", desc: "Complete GL style — MapLibre, Mapbox GL, ArcGIS JS SDK", url: style });
    // Esri needs a style, so a render-less topic gets the greyed row rather than a dead link.
    if (!esriRenders.length) {
      rows.push({
        label: "ArcGIS vector tiles",
        desc: "ArcGIS Pro / AGOL",
        url: "",
        unavailable: "needs a published style — ArcGIS cannot add a layer without one",
      });
    }
    const esri = esriRenders.length ? esriVectorTileUrl(id, chosen, esriRenders) : undefined;
    if (esri) {
      // One service per symbology, so the URL has to name one.
      rows.push({
        label: "ArcGIS vector tiles",
        desc: "ArcGIS Pro / AGOL — the one client that needs its own service contract. Symbology included",
        url: esri,
        pick: esriRenders.length > 1 ? (
          <select value={chosen} onChange={(e) => setPickedRender(e.target.value)}
            title="Which published symbology this service serves"
            className="max-w-[11rem] shrink-0 rounded border border-input bg-card px-1 py-0.5 text-[11px] text-foreground">
            {esriRenders.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        ) : undefined,
      });
    }
  }
  if (ducklake) rows.push({ label: "DuckLake", desc: "Lakehouse table", url: ducklake.href });
  if (!rows.length) return null;
  return (
    <div className="mt-3 rounded-lg border border-border bg-muted p-3">
      <div className="mb-1.5 text-xs font-semibold text-muted-foreground">API &amp; data endpoints</div>
      <div className="flex flex-col gap-1.5">
        {rows.map((r) => (
          <div key={r.label} className={`flex flex-wrap items-center gap-2 text-xs ${r.unavailable ? "opacity-55" : ""}`}>
            <span className={`w-36 shrink-0 font-semibold ${r.unavailable ? "text-muted-foreground" : "text-foreground"}`} title={r.desc}>{r.label}</span>
            {r.pick}
            {r.unavailable ? (
              <span className="min-w-0 flex-1 text-[11px] text-muted-foreground italic">{r.unavailable}</span>
            ) : (
              <>
                <code className="min-w-0 flex-1 truncate rounded bg-card px-1.5 py-0.5 text-[11px] text-muted-foreground" title={r.url}>{r.url}</code>
                <CopyBtn text={r.url} />
                <a href={r.url} target="_blank" rel="noopener" className="text-primary no-underline">open ↗</a>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
