// Coordinates typed into the map search: decimal or DMS latitude/longitude, or UTM zone 12N. Parsed
// with no network. Only points in or near Utah count, which is what lets "-111.7 40.5" and
// "40.5 111.7" read as lat/long in either order.

export type Point = { lat: number; lon: number; label: string };
/** UTM zone 12N (NAD83) metres → [lon, lat]. */
export type FromUtm = (e: number, n: number) => [number, number];

const UTAH = { south: 36.5, north: 42.5, west: -114.6, east: -108.5 };
const inUtah = (lat: number, lon: number) =>
  lat >= UTAH.south && lat <= UTAH.north && lon >= UTAH.west && lon <= UTAH.east;

type Part = { nums: number[]; hemi?: string };

// One coordinate's numbers as degrees: d, d m, or d m s. Only the degrees carry a sign.
function degrees({ nums, hemi }: Part): number | null {
  const [d, m = 0, s = 0] = nums;
  if (nums.length < 1 || nums.length > 3 || m < 0 || m >= 60 || s < 0 || s >= 60) return null;
  if (nums.length > 1 && !Number.isInteger(d)) return null;
  const v = Math.abs(d) + m / 60 + s / 3600;
  return d < 0 || hemi === "S" || hemi === "W" ? -v : v;
}

// Splits the text into two coordinates at a comma, a hemisphere letter, or half way.
function parts(text: string): Part[] | null {
  const tokens = text.toUpperCase().match(/-?\d+(?:\.\d+)?|[NSEW]|,|[^\s\d.,°º˚'′’"″:NSEW-]/g) ?? [];
  if (tokens.some((t) => !/^(-?\d|[NSEW,])/.test(t))) return null;
  const out: Part[] = [];
  let cur: Part = { nums: [] };
  const close = () => { if (cur.nums.length) out.push(cur); cur = { nums: [] }; };
  for (const t of tokens) {
    if (t === ",") close();
    else if (/[NSEW]/.test(t)) {
      if (cur.nums.length && !cur.hemi) { cur.hemi = t; close(); }
      else { close(); cur.hemi = t; }
    } else cur.nums.push(Number(t));
  }
  close();
  if (out.length === 1 && out[0].nums.length % 2 === 0 && !out[0].hemi) {
    const { nums } = out[0];
    return [{ nums: nums.slice(0, nums.length / 2) }, { nums: nums.slice(nums.length / 2) }];
  }
  return out.length === 2 ? out : null;
}

/** Decimal or DMS latitude and longitude, in either order when Utah makes it unambiguous. */
export function parseLatLon(text: string): Point | null {
  const p = parts(text);
  if (!p) return null;
  const [a, b] = p.map(degrees);
  if (a === null || b === null) return null;
  const latFirst = p[0].hemi ? "NS".includes(p[0].hemi) : p[1].hemi ? "EW".includes(p[1].hemi) : undefined;
  // West is implied: Utah is all west of Greenwich, and people often drop the sign.
  const west = (v: number, part: Part) => (part.hemi || v < 0 ? v : -v);
  for (const first of latFirst === undefined ? [true, false] : [latFirst]) {
    const lat = first ? a : b;
    const lon = first ? west(b, p[1]) : west(a, p[0]);
    if (inUtah(lat, lon)) return { lat, lon, label: `${lat.toFixed(5)}, ${lon.toFixed(5)}` };
  }
  return null;
}

const ZONE = String.raw`(?:(?:zone\s*)?12\s*[c-x]?\s+)?`;
const UTM_EN = new RegExp(String.raw`^${ZONE}(\d{6}(?:\.\d+)?)\s*m?\s*e?[\s,]+(\d{7}(?:\.\d+)?)\s*m?\s*n?$`, "i");
const UTM_NE = new RegExp(String.raw`^${ZONE}(\d{7}(?:\.\d+)?)\s*m?\s*n[\s,]+(\d{6}(?:\.\d+)?)\s*m?\s*e$`, "i");

/** UTM zone 12N easting and northing ("12T 435000 4490000", "4490000N 435000E"), or null. */
export function parseUtm(text: string): { e: number; n: number } | null {
  const t = text.trim();
  const en = UTM_EN.exec(t);
  if (en) return { e: Number(en[1]), n: Number(en[2]) };
  const ne = UTM_NE.exec(t);
  return ne ? { e: Number(ne[2]), n: Number(ne[1]) } : null;
}

export function utmPoint(text: string, fromUtm: FromUtm): Point | null {
  const u = parseUtm(text);
  if (!u) return null;
  const [lon, lat] = fromUtm(u.e, u.n);
  return inUtah(lat, lon) ? { lat, lon, label: `UTM 12N ${u.e} E, ${u.n} N` } : null;
}

/** proj4's zone 12N inverse. The search box loads it on mount, so it is cached for offline use. */
export async function loadFromUtm(): Promise<FromUtm> {
  const { default: proj4 } = await import("proj4");
  const t = proj4("+proj=utm +zone=12 +ellps=GRS80 +units=m +no_defs", "EPSG:4326");
  return (e, n) => t.forward([e, n]) as [number, number];
}
