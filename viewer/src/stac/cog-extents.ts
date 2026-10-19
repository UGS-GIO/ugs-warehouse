import { useQueries } from "@tanstack/react-query";
import { qk } from "@/query-keys";

// ---- COG (GeoTIFF) extents, read from the cog:// metadata, keyed + cached by href ----
// staleTime:Infinity (a COG's extent is immutable); retry:1 so a transient blip on a bbox-less COG
// isn't cached as a permanent failure. Returns href→bbox for those that resolved.
async function fetchCogBox(cogHref: string): Promise<[number, number, number, number] | null> {
  const { ensureCogProtocol } = await import("@/map/cog");
  await ensureCogProtocol();
  const { getCogMetadata } = await import("@geomatico/maplibre-cog-protocol");
  const meta = await getCogMetadata(cogHref);
  const bb = meta?.bbox ? (meta.bbox as number[]).slice(0, 4) : null;
  return (bb && bb.length >= 4 ? bb : null) as [number, number, number, number] | null;
}
export function useCogBoxes(hrefs: (string | undefined)[]): Record<string, [number, number, number, number]> {
  const urls = [...new Set(hrefs.filter((h): h is string => Boolean(h)))];
  const results = useQueries({
    queries: urls.map((href) => ({
      queryKey: qk.cogBbox(href),
      queryFn: () => fetchCogBox(href),
      staleTime: Infinity,
      retry: 1,
    })),
  });
  const out: Record<string, [number, number, number, number]> = {};
  urls.forEach((href, i) => { const bb = results[i].data; if (bb) out[href] = bb; });
  return out;
}
