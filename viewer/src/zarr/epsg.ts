/**
 * Resolve the store's CRS locally. geozarr's proj schema prefers `proj:code` and drops `proj:wkt2`,
 * which sends the layer to epsg.io over the network at init; our stores already ship the wkt2, so
 * parse it here and keep the fetch as the fallback.
 */
import { epsgResolver, parseWkt } from "@developmentseed/proj";

export function makeLocalEpsgResolver(attrs: Record<string, unknown>): typeof epsgResolver {
  const wkt2 = attrs["proj:wkt2"];
  if (typeof wkt2 !== "string" || wkt2.length === 0) return epsgResolver;
  return async (epsg: number) => {
    try {
      return parseWkt(wkt2);
    } catch (e) {
      console.warn(`proj:wkt2 unusable for EPSG:${epsg}; falling back to epsg.io`, e);
      return epsgResolver(epsg);
    }
  };
}
