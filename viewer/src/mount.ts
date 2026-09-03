// Where the bundle is mounted. One tree, three mount points: "/" on Firebase Hosting,
// "/review/viewer/" for the IAP review build, "/viewer/pr-<n>/" for a PR preview. Vite's `base`
// carries it (it is what rewrites the asset URLs), so the router reads it from there rather than
// sniffing the path — a path route like /review/viewer/map is indistinguishable from a mount at
// /review/viewer/map at runtime.
export const toBasepath = (baseUrl: string) => baseUrl.replace(/\/+$/, "") || "/";

/** An in-app URL for a path route, mount-prefixed so it survives a subpath deploy. Real <a> hrefs
 *  need this: a modifier/middle-click bypasses the router and hits the server with the raw path. */
export const mountHref = (path: string, search?: URLSearchParams, baseUrl = import.meta.env.BASE_URL) => {
  const base = toBasepath(baseUrl);
  const url = (base === "/" ? "" : base) + path;
  const q = search?.toString();
  return q ? `${url}?${q}` : url;
};
