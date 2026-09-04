// Where the bundle is mounted. One tree, three mount points: "/" on Firebase Hosting,
// "/review/viewer/" for the IAP review build, "/viewer/pr-<n>/" for a PR preview. Vite's `base`
// carries it (it is what rewrites the asset URLs), so the router reads it from there rather than
// sniffing the path — a path route like /review/viewer/map is indistinguishable from a mount at
// /review/viewer/map at runtime.
export const toBasepath = (baseUrl: string) => baseUrl.replace(/\/+$/, "") || "/";

