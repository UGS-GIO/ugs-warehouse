import { useQueries, useQuery } from "@tanstack/react-query";
import { qk } from "@/query-keys";

// ---- MapLibre GL style layers (the `layers` array of a bound style_url) ----
// Via TanStack Query so it aborts on unmount, dedupes, and caches. null = loading/error (→ neutral).
async function fetchStyleLayers(url: string, signal?: AbortSignal): Promise<Record<string, unknown>[] | null> {
  const r = await fetch(url, { signal });
  const d = await r.json();
  return Array.isArray(d?.layers) ? (d.layers as Record<string, unknown>[]) : null;
}
const styleLayersQuery = (url?: string) => ({
  queryKey: qk.styleLayers(url),
  queryFn: ({ signal }: { signal?: AbortSignal }) => fetchStyleLayers(url as string, signal),
});
export function useStyleLayers(styleUrl?: string): Record<string, unknown>[] | null {
  const { data } = useQuery({ ...styleLayersQuery(styleUrl), enabled: Boolean(styleUrl) });
  return styleUrl ? (data ?? null) : null;
}
// ---- Live ugs-styles legend (icon renders) ----
// An icon render bakes its colors into a sprite PNG, so `legendFromStyle` has no paint to read and
// the legend has to come from ugs-styles. The copy on the STAC item is a SNAPSHOT taken whenever
// `attach_renders` last ran, so it goes stale the moment a style publishes — read the published
// manifest instead, which is the same source the snapshot was made from, minus the staleness.
//
// The manifest sits two levels above a render's style_url:
//   .../styles/styles/<layer>/<render>.json  ->  .../styles/index.json
const manifestUrlOf = (styleUrl: string): string | undefined => {
  try { return new URL("../../index.json", styleUrl).href; } catch { return undefined; }
};

type ManifestEntry = {
  itemId?: string; render?: string; field?: string;
  legend?: { label: string; color: string; values?: { value: string; color: string; label?: string }[] }[];
};

// `no-cache` forces a revalidation rather than trusting the manifest's max-age — a publish that
// just landed should show up here immediately, which is the whole point of reading it live.
async function fetchStylesManifest(url: string, signal?: AbortSignal): Promise<ManifestEntry[]> {
  const r = await fetch(url, { signal, cache: "no-cache" });
  const d = await r.json();
  return Array.isArray(d) ? (d as ManifestEntry[]) : [];
}

/**
 * The published legend for one (item, render), read live from ugs-styles. Returns undefined while
 * loading, on failure, or when the manifest has no entry — callers fall back to the STAC snapshot.
 */
export function useLiveLegend(styleUrl: string | undefined, itemId: string | undefined, renderId: string | undefined) {
  const url = styleUrl ? manifestUrlOf(styleUrl) : undefined;
  const { data } = useQuery({
    queryKey: qk.stylesManifest(url),
    queryFn: ({ signal }) => fetchStylesManifest(url as string, signal),
    enabled: Boolean(url),
    staleTime: 60_000,
  });
  if (!data || !itemId || !renderId) return undefined;
  const hit = data.find((e) => e.itemId === itemId && e.render === renderId);
  return hit?.legend?.length ? { entries: hit.legend, field: hit.field } : undefined;
}

/** Same, for a set of layers at once (Map view overlays). Returns id→layers for those that resolved. */
export function useStyleLayersFor(layers: { id: string; styleUrl?: string }[]): Record<string, Record<string, unknown>[]> {
  const withStyle = layers.filter((l) => l.styleUrl);
  const results = useQueries({ queries: withStyle.map((l) => styleLayersQuery(l.styleUrl)) });
  const out: Record<string, Record<string, unknown>[]> = {};
  withStyle.forEach((l, i) => { const d = results[i].data; if (d) out[l.id] = d; });
  return out;
}
