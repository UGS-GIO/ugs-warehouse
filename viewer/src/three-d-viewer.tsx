// 3D fence-diagram viewer: GeoParquet/GeoJSON cross-sections draped over a 3DEP terrain mesh.
import { COORDINATE_SYSTEM, OrbitView } from "@deck.gl/core";
import { PathStyleExtension } from "@deck.gl/extensions";
import { BitmapLayer, PathLayer, SolidPolygonLayer } from "@deck.gl/layers";
import { SimpleMeshLayer } from "@deck.gl/mesh-layers";
import DeckGL from "@deck.gl/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { lruSet } from "./lru";
import { type Asset, classificationColors, cogAsset, type StacDoc } from "./stac";
import { buildMeshFrom3DEP, type TerrainMesh } from "./terrain";
import { UiSlider } from "./ui/slider";

const GEOLOGIC_COLORS: Record<string, string> = {
  "red pine shale": "#556B2F",
  "zur": "#556B2F",
  "weber sandstone": "#EEDC82",
  "ipw": "#EEDC82",
  "gardison limestone": "#4682B4",
  "mg": "#4682B4",
  "deseret limestone": "#B0C4DE",
  "md": "#B0C4DE",
  "humbug formation": "#D2B48C",
  "mh": "#D2B48C",
  "keetley volcanics": "#BA55D3",
  "tk": "#BA55D3",
  "alluvium": "#FFFACD",
  "qal": "#FFFACD",
  "glacial till": "#DCDCDC",
  "qg": "#DCDCDC",
};

// HSL → hex so every unit color is a hex string — deck.gl needs RGB tuples (see hexToRgb), and a
// single format keeps the legend swatch and the 3D fill in sync.
function hslToHex(h: number, s: number, l: number): string {
  s /= 100; l /= 100;
  const k = (n: number) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const c = l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return Math.round(255 * c).toString(16).padStart(2, "0");
  };
  return `#${f(0)}${f(8)}${f(4)}`;
}

function getUnitColor(unit: string, label: string): string {
  const u = (unit ?? "").toLowerCase().trim();
  const l = (label ?? "").toLowerCase().trim();
  if (GEOLOGIC_COLORS[u]) return GEOLOGIC_COLORS[u];
  if (GEOLOGIC_COLORS[l]) return GEOLOGIC_COLORS[l];
  // Standard string hashing for stable geologic pastel color
  let hash = 0;
  const str = u || l || "unknown";
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  return hslToHex(Math.abs(hash) % 360, 65, 60);
}

// "#rrggbb" → [r,g,b]; deck.gl color accessors want a numeric tuple, not a CSS string.
function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim());
  if (!m) return [128, 128, 128];
  return [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)];
}

// Parsed fence + sampled terrain are expensive (network fetch/parse; ~tens of 3DEP requests) and the
// viewer re-mounts every time the 3D tab is selected. Cache both per item.id at module scope so a
// second visit is instant instead of re-doing all the work.
type FenceData = {
  polygons: { unit: string; rings: number[][][] }[];
  lines: { kind: "fault" | "contact" | "boundary"; dashed: boolean; path: number[][] }[];
  legend: { unit: string; label: string; color: string }[];
  parquetFill: Record<string, string>;
  extent: { spanXY: number; zTop: number; zMid: number; half: [number, number]; bbox: [number, number, number, number]; center: [number, number]; scale: [number, number] };
};
// 3D fence + terrain meshes are multi-MB each; LRU-cap (see ./lru) so orbiting many 3D pubs can't
// grow the heap unbounded.
const FENCE_CACHE_CAP = 3;
const TERRAIN_CACHE_CAP = 3;
const fenceCache = new Map<string, FenceData>();
const terrainCache = new Map<string, TerrainMesh | null>();

export function ThreeDViewer({ asset, item }: { asset: Asset; item: StacDoc }) {
  const itemId = item.id ?? asset.href;  // stable cache key (item.id is optional on StacDoc)
  const [loading, setLoading] = useState(() => !fenceCache.has(itemId));
  const [error, setError] = useState<string | null>(null);
  // Terrain runs after the fence draws; track it so the canvas can show a real progress indicator.
  const [terrainPending, setTerrainPending] = useState(false);

  // Fence parsed to LOCAL metres centred on the dataset. OrbitView is a Cartesian 3D camera (unlike
  // maplibre's 2.5D map camera) so it can orbit freely — including under the surface to look up at
  // the slice, which is the whole point of a fence diagram.
  const [polygons, setPolygons] = useState<{ unit: string; rings: number[][][] }[]>([]);
  const [lines, setLines] = useState<{ kind: "fault" | "contact" | "boundary"; dashed: boolean; path: number[][] }[]>([]);
  const [legend, setLegend] = useState<{ unit: string; label: string; color: string }[]>([]);
  const [extent, setExtent] = useState<{ spanXY: number; zTop: number; zMid: number; half: [number, number]; bbox: [number, number, number, number]; center: [number, number]; scale: [number, number] } | null>(null);
  const [terrainMesh, setTerrainMesh] = useState<TerrainMesh | null>(null);
  // Authored geologic colors (MapUnit → hex) from the publication's ArcGIS symbology — the real
  // cartography. Interim: a baked per-pub sidecar (the 3D pipeline will fold this into the GeoParquet).
  // Authored geologic colors for this pub (interim baked sidecar). Absent → getUnitColor fallback.
  const { data: authored = {} } = useQuery<Record<string, string>>({
    queryKey: ["3d-colors", item.id],
    queryFn: async ({ signal }) => {
      const r = await fetch(`${import.meta.env.BASE_URL}3d-colors/${item.id}.json`, { signal });
      return r.ok ? r.json() : {};
    },
    staleTime: 5 * 60_000,
  });
  // Per-unit fill carried in the GeoParquet `fill` column (cloud-native path) — authored, highest
  // precedence. Empty on the GeoJSON path.
  const [parquetFill, setParquetFill] = useState<Record<string, string>>({});

  const [vex, setVex] = useState(2.5);
  const [showUnits, setShowUnits] = useState(true);
  const [showLines, setShowLines] = useState(true);
  const [showSheet, setShowSheet] = useState(true);
  const [hovered, setHovered] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const polyUrl = asset.href;
  const lineUrl = polyUrl.replace("_3d_polygons.geojson", "_3d_lines.geojson");
  const cog = cogAsset(item);
  // Drape the geologic map sheet — the COG's PNG overview (browsers can't texture a COG directly).
  const sheetImg = cog ? cog.href.replace(/\.cog\.tif$/i, ".thumb.png") : undefined;

  useEffect(() => {
    let active = true;
    const ac = new AbortController();
    // Cache hit → restore parsed fence synchronously, skip the fetch/parse entirely.
    const hit = fenceCache.get(itemId);
    if (hit) {
      setPolygons(hit.polygons); setLines(hit.lines); setLegend(hit.legend);
      setParquetFill(hit.parquetFill); setExtent(hit.extent);
      setError(null); setLoading(false);
      return () => { active = false; };
    }
    setLoading(true);
    setError(null);

    // A fence feature, normalised across both sources: GeoJSON geometry (with Z) + flat props.
    type Feat = { geometry: { type?: string; coordinates?: unknown } | null; props: Record<string, unknown> };

    // Cloud-native first: 3D GeoParquet (duckdb-wasm, WKB-Z) — ONLY when the item carries a real
    // `.parquet` 3d-vector asset (the pipeline output). No same-origin probing: a missing file makes
    // duckdb throw, which the route error-boundary would catch and reset the URL. Else: GeoJSON.
    async function loadFence(): Promise<{ polyFeats: Feat[]; lineFeats: Feat[]; parquet: boolean }> {
      const assets = Object.values(item.assets ?? {}) as Asset[];
      const pq = (re: RegExp) => assets.find((a) => /\.parquet$/i.test(a.href) && re.test(a.href))?.href;
      const polyPq = pq(/polygon/i), linePq = pq(/line/i);
      if (polyPq) {
        try {
          const { readFeatures3D } = await import("./download");
          const [pf, lf] = await Promise.all([
            readFeatures3D(polyPq, ac.signal),
            linePq ? readFeatures3D(linePq, ac.signal).catch(() => []) : Promise.resolve([]),
          ]);
          if (pf.length) return { polyFeats: pf as Feat[], lineFeats: lf as Feat[], parquet: true };
        } catch { /* parquet read failed → GeoJSON */ }
      }
      const [pd, ld] = await Promise.all([
        fetch(polyUrl, { signal: ac.signal }).then((r) => { if (!r.ok) throw new Error("Polygons failed to load"); return r.json(); }),
        fetch(lineUrl, { signal: ac.signal }).then((r) => r.json()).catch(() => null),
      ]);
      const toFeat = (f: { geometry?: unknown; properties?: unknown }): Feat =>
        ({ geometry: (f.geometry ?? null) as Feat["geometry"], props: (f.properties ?? {}) as Record<string, unknown> });
      return {
        polyFeats: ((pd?.features ?? []) as { geometry?: unknown; properties?: unknown }[]).map(toFeat),
        lineFeats: ((ld?.features ?? []) as { geometry?: unknown; properties?: unknown }[]).map(toFeat),
        parquet: false,
      };
    }

    loadFence()
      .then(({ polyFeats, lineFeats }) => {
        if (!active) return;
        // Pass 1: collect raw [lon,lat,z] + bounds (centre needed before the local-metre projection).
        const rawPolys: { unit: string; rings: number[][][] }[] = [];
        const rawLines: { kind: "fault" | "contact" | "boundary"; dashed: boolean; path: number[][] }[] = [];
        const legendMap = new Map<string, string>();
        const pFill: Record<string, string> = {};
        let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
        let minZ = Infinity, maxZ = -Infinity, count = 0;
        const scan = (lon: number, lat: number, z: number) => {
          minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
          minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
          minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z); count++;
        };
        const xyz = (pt: number[]): number[] => { const c = [pt[0], pt[1], pt[2] ?? 0]; scan(c[0], c[1], c[2]); return c; };

        for (const feat of polyFeats) {
          const p = feat.props;
          const unit = String(p.MapUnit ?? p.unit ?? "Unknown Unit");
          const label = String(p.label ?? p.Label ?? unit);
          if (typeof p.fill === "string") pFill[unit] = p.fill; // authored fill from the parquet
          const geom = feat.geometry || {};
          const multi: number[][][][] = geom.type === "MultiPolygon" ? geom.coordinates as number[][][][]
            : geom.type === "Polygon" ? [geom.coordinates as number[][][]] : [];
          for (const rings of multi) {
            const r3 = rings.map((ring) => ring.map(xyz));
            if (r3.length && r3[0].length) { rawPolys.push({ unit, rings: r3 }); legendMap.set(unit, label); }
          }
        }
        for (const feat of lineFeats) {
          const p = feat.props;
          // Authored line cartography from the GeMS Type/Symbol (parquet carries kind/dashed directly).
          const type = String(p.Type ?? "").toLowerCase();
          const sym = String(p.Symbol ?? "").toLowerCase();
          const kind: "fault" | "contact" | "boundary" = p.kind === "fault" || p.kind === "boundary" || p.kind === "contact"
            ? p.kind as "fault" | "contact" | "boundary"
            : type.includes("fault") ? "fault" : type.includes("boundary") ? "boundary" : "contact";
          const dashed = typeof p.dashed === "boolean" ? p.dashed : sym.includes("approxim");
          const geom = feat.geometry || {};
          const multi: number[][][] = geom.type === "MultiLineString" ? geom.coordinates as number[][][]
            : geom.type === "LineString" ? [geom.coordinates as number[][]] : [];
          for (const coords of multi) {
            const path = coords.map(xyz);
            if (path.length) rawLines.push({ kind, dashed, path });
          }
        }
        if (!count) throw new Error("No valid coordinates found in the 3D dataset");

        // Pass 2: lon/lat → local metres centred on the dataset (z stays absolute-elevation metres).
        const cLon = (minLon + maxLon) / 2, cLat = (minLat + maxLat) / 2;
        const mLon = 111320 * Math.cos((cLat * Math.PI) / 180), mLat = 110574;
        const toLocal = (p: number[]): number[] => [(p[0] - cLon) * mLon, (p[1] - cLat) * mLat, p[2]];

        const halfX = ((maxLon - minLon) / 2) * mLon, halfY = ((maxLat - minLat) / 2) * mLat;
        const fence: FenceData = {
          polygons: rawPolys.map((d) => ({ ...d, rings: d.rings.map((r) => r.map(toLocal)) })),
          lines: rawLines.map((l) => ({ ...l, path: l.path.map(toLocal) })),
          legend: Array.from(legendMap.entries()).map(([unit, label]) => ({ unit, label, color: "" })).sort((a, b) => a.unit.localeCompare(b.unit)),
          parquetFill: pFill,
          extent: {
            spanXY: Math.max(halfX, halfY) * 2, zTop: maxZ, zMid: (minZ + maxZ) / 2, half: [halfX, halfY],
            bbox: [minLon, minLat, maxLon, maxLat], center: [cLon, cLat], scale: [mLon, mLat],
          },
        };
        lruSet(fenceCache, String(itemId), fence, FENCE_CACHE_CAP);
        if (!active) return;
        setParquetFill(fence.parquetFill);
        setPolygons(fence.polygons);
        setLines(fence.lines);
        setLegend(fence.legend);
        setExtent(fence.extent);
        setLoading(false);
      })
      .catch((err) => { if (active && !ac.signal.aborted) { setError(err instanceof Error ? err.message : "Failed to load 3D data files"); setLoading(false); } });

    return () => { active = false; ac.abort(); };
  }, [item.id, polyUrl, lineUrl]);

  // Build the DEM terrain mesh once the dataset extent is known. Span the MAP-SHEET bbox (item.bbox),
  // not the fence bbox — the fence is only a transect (~40% of the quad), so draping the full-quad COG
  // over the fence extent would mis-size + misregister it. Built in the fence's local frame so the
  // fence sits as a transect within the full-size map; the COG textures it correctly.
  useEffect(() => {
    if (!extent) { setTerrainMesh(null); return; }
    let active = true;
    // Cache hit → reuse the sampled mesh, skip the ~tens of 3DEP requests.
    if (terrainCache.has(itemId)) {
      setTerrainMesh(terrainCache.get(itemId) ?? null);
      setTerrainPending(false);
      return () => { active = false; };
    }
    const mapBbox = (item.bbox?.slice(0, 4) as [number, number, number, number] | undefined) ?? extent.bbox;
    // Live USGS 3DEP (CORS-open, public domain, 1 m lidar over Utah) — no hosting, any pub's bbox.
    // Progressive: a coarse grid lands in ~1–2 s so the surface shows immediately, then a fine grid
    // samples in the background and swaps in (smooth — no facets, the draped sheet stops looking
    // tessellated). Cache the fine result so revisits skip both passes.
    setTerrainPending(true);
    const ac = new AbortController();
    (async () => {
      try {
        const coarse = await buildMeshFrom3DEP(mapBbox, extent.center, extent.scale, 48, ac.signal);
        if (!active) return;
        if (coarse) setTerrainMesh(coarse);
        const fine = await buildMeshFrom3DEP(mapBbox, extent.center, extent.scale, 160, ac.signal);
        if (ac.signal.aborted) return;  // don't cache a half-sampled (aborted) mesh
        lruSet(terrainCache, String(itemId), fine ?? coarse, TERRAIN_CACHE_CAP);
        if (!active) return;
        if (fine ?? coarse) setTerrainMesh(fine ?? coarse);
      } catch { if (active) setTerrainMesh(null); }
      finally { if (active) setTerrainPending(false); }
    })();
    return () => { active = false; ac.abort(); };
  }, [extent, item]);

  // Unit colour, standard-first: STAC classification:classes (the warehouse's built-in mechanism, what
  // the 3D pipeline will stamp) → interim per-pub sidecar → derived placeholder.
  const clsColors = useMemo(() => classificationColors(item), [item]);
  // Precedence: GeoParquet `fill` column → STAC classification:classes → interim sidecar → placeholder.
  const colorOf = (unit: string) => parquetFill[unit] ?? clsColors[unit] ?? authored[unit] ?? getUnitColor(unit, "");

  const layers = useMemo(() => {
    const out: unknown[] = [];
    if (showSheet && extent && terrainMesh) {
      // Terrain surface: DEM mesh, exaggerated via getScale (z only) to match the fence, draped with
      // the geologic map sheet. getColor white = show the texture as-is.
      out.push(new SimpleMeshLayer({
        id: "terrain",
        data: [{ position: [0, 0, 0] }],
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        mesh: terrainMesh as never,
        texture: sheetImg,
        getPosition: () => [0, 0, 0],
        getColor: [255, 255, 255],
        getScale: [1, 1, vex],
        material: false,
        updateTriggers: { getScale: [vex] },
      }) as unknown);
    } else if (showSheet && sheetImg && extent) {
      // Fallback flat plane while the DEM mesh loads (or where there's no terrarium coverage).
      const z = extent.zTop * vex, [hx, hy] = extent.half;
      out.push(new BitmapLayer({
        id: "map-sheet",
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        image: sheetImg,
        bounds: [[-hx, -hy, z], [-hx, hy, z], [hx, hy, z], [hx, -hy, z]] as never,
        opacity: 0.9,
      }) as unknown);
    }
    if (showUnits) {
      out.push(new SolidPolygonLayer({
        id: "fence-units",
        data: polygons,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        _full3d: true,
        getPolygon: ((d: { rings: number[][][] }) => d.rings.map((ring) => ring.map((p) => [p[0], p[1], p[2] * vex]))) as never,
        getFillColor: (d: { unit: string }) => {
          const [r, g, b] = hexToRgb(colorOf(d.unit));
          const a = hovered ? (d.unit === hovered ? 240 : 55) : 200;
          return [r, g, b, a];
        },
        pickable: true,
        onHover: (info: { object?: { unit: string } }) => setHovered(info?.object?.unit ?? null),
        updateTriggers: { getPolygon: [vex], getFillColor: [hovered, authored, clsColors, parquetFill] },
      }) as unknown);
    }
    if (showLines) {
      out.push(new PathLayer({
        id: "fence-lines",
        data: lines,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        getPath: ((d: { path: number[][] }) => d.path.map((p) => [p[0], p[1], p[2] * vex])) as never,
        // Authored: all black; faults heavier than contacts, section boundary thin.
        getColor: [25, 25, 25],
        getWidth: (d: { kind: string }) => (d.kind === "fault" ? 2.6 : d.kind === "boundary" ? 0.8 : 1.3),
        // Dashed = "approximately located" (geologic convention); solid = well located.
        getDashArray: (d: { dashed: boolean }) => (d.dashed ? [5, 3] : [0, 0]),
        dashJustified: true,
        extensions: [new PathStyleExtension({ dash: true })],
        widthUnits: "pixels",
        widthMinPixels: 1,
        updateTriggers: { getPath: [vex] },
      }) as unknown);
    }
    return out;
  }, [polygons, lines, showUnits, showLines, showSheet, sheetImg, extent, terrainMesh, vex, hovered, authored, clsColors, parquetFill]);

  if (loading) return <div className="mt-2 text-sm text-muted-foreground p-8 text-center bg-muted/20 border border-border rounded-lg">Loading 3D subsurface geometries…</div>;
  if (error) return <div className="mt-2 text-sm text-destructive p-4 bg-destructive/10 border border-destructive/20 rounded-lg">Failed to render 3D Fence Diagram: {error}</div>;

  // Frame the full map sheet (so the fence reads as a transect within it), centred on the map — not
  // the fence — since the fence sits off-centre in the quad.
  const mb = item.bbox?.slice(0, 4) as [number, number, number, number] | undefined;
  const mapCtr: [number, number] = mb && extent
    ? [((mb[0] + mb[2]) / 2 - extent.center[0]) * extent.scale[0], ((mb[1] + mb[3]) / 2 - extent.center[1]) * extent.scale[1]]
    : [0, 0];
  const mapSpan = mb && extent ? Math.max((mb[2] - mb[0]) * extent.scale[0], (mb[3] - mb[1]) * extent.scale[1]) : extent?.spanXY ?? 1;
  const initialViewState = {
    target: [mapCtr[0], mapCtr[1], (extent?.zMid ?? 0) * vex] as [number, number, number],
    rotationX: 25,    // pitch above the horizon
    rotationOrbit: -25, // azimuth
    zoom: Math.log2(520 / Math.max(mapSpan, 1)),
    minZoom: -12, maxZoom: 40,
  };

  return (
    <div className="mt-2 flex flex-col md:flex-row gap-4 border border-border rounded-lg bg-card overflow-hidden h-[640px]">
      {/* Free 3D orbit scene (deck.gl OrbitView) — Cartesian, flies under the surface. */}
      <div className="flex-1 relative bg-[#0F172A] h-[400px] md:h-full">
        <DeckGL
          views={new OrbitView({ orbitAxis: "Z" })}
          initialViewState={initialViewState}
          controller={true}
          layers={layers as never}
          getCursor={() => "grab"}
          style={{ position: "relative", width: "100%", height: "100%" }}
        />
        <div className="absolute top-3 left-3 bg-card/85 backdrop-blur-sm border border-border p-2.5 rounded-md shadow-sm text-xs pointer-events-none max-w-[230px]">
          <div className="font-semibold text-foreground border-b border-border pb-1 mb-1 flex items-center gap-1.5">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-primary animate-pulse" />
            Free 3D Orbit
          </div>
          <div className="text-muted-foreground leading-snug">
            Drag to orbit (rotate under the surface)<br />
            Scroll to zoom · right-drag to pan
          </div>
        </div>
        {terrainPending && (
          <div className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-card/90 backdrop-blur-sm border border-border px-3 py-2 rounded-md shadow-sm text-xs text-foreground">
            <span className="inline-block w-3.5 h-3.5 rounded-full border-2 border-primary border-t-transparent animate-spin" />
            {terrainMesh ? "Refining terrain…" : "Sampling USGS 3DEP terrain…"}
          </div>
        )}
      </div>

      {/* Control sidebar + geologic legend */}
      <div className="w-full md:w-[320px] bg-background border-t md:border-t-0 md:border-l border-border p-4 flex flex-col gap-4 overflow-y-auto h-[240px] md:h-full">
        <div className="border-b border-border pb-3">
          <h3 className="font-semibold text-xs text-foreground uppercase tracking-wider mb-2.5">Display Settings</h3>
          <div className="flex flex-col gap-2 text-xs">
            <label className="flex items-center gap-2 text-foreground cursor-pointer">
              <input type="checkbox" checked={showUnits} onChange={(e) => setShowUnits(e.target.checked)} className="rounded border-border text-primary focus:ring-primary" />
              <span>Stratigraphic units (3D)</span>
            </label>
            <label className="flex items-center gap-2 text-foreground cursor-pointer">
              <input type="checkbox" checked={showLines} onChange={(e) => setShowLines(e.target.checked)} className="rounded border-border text-primary focus:ring-primary" />
              <span>Contacts &amp; faults (3D)</span>
            </label>
            {sheetImg && (
              <label className="flex items-center gap-2 text-foreground cursor-pointer">
                <input type="checkbox" checked={showSheet} onChange={(e) => setShowSheet(e.target.checked)} className="rounded border-border text-primary focus:ring-primary" />
                <span>Geologic map sheet (draped)</span>
              </label>
            )}
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground">{terrainMesh ? "Map sheet drapes the USGS 3DEP terrain (1 m lidar); fence tops meet the ground." : "Sampling USGS 3DEP terrain…"}</p>
        </div>

        <div className="border-b border-border pb-3">
          <div className="flex justify-between items-center text-xs mb-1.5">
            <span className="font-semibold text-foreground uppercase tracking-wider">Vertical Exaggeration</span>
            <span className="text-muted-foreground font-mono">{vex.toFixed(1)}x</span>
          </div>
          <UiSlider value={vex} onValueChange={setVex} min={0.5} max={5} step={0.1} label="Vertical exaggeration" />
        </div>

        <div className="flex-1 flex flex-col min-h-0">
          <div className="flex justify-between items-center text-xs mb-2">
            <h3 className="font-semibold text-foreground uppercase tracking-wider">Geologic Legend</h3>
            <span className="text-[10px] text-muted-foreground font-mono">{legend.length} units</span>
          </div>
          <input type="text" placeholder="Filter units..." value={search} onChange={(e) => setSearch(e.target.value)}
            className="w-full text-xs border border-border bg-card px-2.5 py-1.5 rounded mb-2.5 focus:outline-none focus:border-primary" />
          <div className="flex-1 overflow-y-auto pr-1 flex flex-col gap-1.5 max-h-[220px] md:max-h-none">
            {legend
              .filter((l) => !search || l.unit.toLowerCase().includes(search.toLowerCase()) || l.label.toLowerCase().includes(search.toLowerCase()))
              .map((l) => (
                <div key={l.unit} onMouseEnter={() => setHovered(l.unit)} onMouseLeave={() => setHovered(null)}
                  className={`flex items-start gap-2.5 p-1.5 rounded border text-xs cursor-default transition ${hovered === l.unit ? "border-primary bg-primary/5 font-medium" : "border-transparent hover:bg-muted"}`}>
                  <span className="inline-block w-4 h-4 rounded border border-black/10 shrink-0" style={{ backgroundColor: colorOf(l.unit) }} />
                  <div className="flex-1 leading-snug">
                    <span className="font-bold font-mono mr-1.5">{l.label}</span>
                    <span className="text-foreground">{l.unit}</span>
                  </div>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

