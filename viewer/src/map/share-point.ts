// A point to copy or share: its coordinates as text, and a link that opens the map there with a pin.

/** "40.55940, -111.70000": latitude first, as phones and GPS units read them. */
export const formatCoords = (lat: number, lng: number) => `${lat.toFixed(5)}, ${lng.toFixed(5)}`;

/** This page's link with the camera on the point (`m`, as the map writes it) and a pin there. */
export function pointLink(href: string, lng: number, lat: number, zoom: number): string {
  const url = new URL(href);
  url.searchParams.set("m", `${lng.toFixed(5)},${lat.toFixed(5)},${zoom.toFixed(2)}`);
  url.searchParams.set("pin", `${lat.toFixed(5)},${lng.toFixed(5)}`);
  return url.toString();
}

/** The pin a shared link carries, or null. */
export function pinOf(search: string): { lng: number; lat: number } | null {
  const parts = (new URLSearchParams(search).get("pin") ?? "").split(",");
  if (parts.length !== 2 || parts.some((p) => !p.trim())) return null;
  const [lat, lng] = parts.map(Number);
  return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? { lng, lat } : null;
}
