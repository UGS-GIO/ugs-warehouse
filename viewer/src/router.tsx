// TanStack Router setup. The viewer is a static SPA on GCS (served at /warehouse/viewer/ with no
// server-side rewrites), so we use ONE root route + typed search params rather than path routes —
// every URL stays /warehouse/viewer/?…, which always resolves to index.html (no 404 on reload/deep
// link), while the router gives us typed search, history, and clean per-view navigation.
import { createRootRoute, createRouter } from "@tanstack/react-router";

import { App } from "./App";

const NAV_VIEWS = ["map", "search", "arch", "guide"] as const;  // "catalog" is the param-less default
const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

// Nav state lives in the URL search. view/c/i/l/s are typed; any OTHER param (catalog, m, ftsdb,
// vssdb, models, extrepo, features, searchCorpus — read directly by other modules) passes through
// untouched, so override/spike links keep working across navigation.
const rootRoute = createRootRoute({
  component: App,
  validateSearch: (s: Record<string, unknown>) => ({
    ...s,
    view: NAV_VIEWS.includes(s.view as never) ? (s.view as string) : undefined,
    c: str(s.c), i: str(s.i), l: str(s.l), s: str(s.s),
  }),
});

// The viewer is served under a sub-path in prod (`/warehouse/viewer/`) but at `/` in dev, and vite's
// `base: "./"` keeps it portable to any path. Without a basepath the router defaults to `/` and
// navigation rewrites the URL to `/?…`, dropping the sub-path (so reloads 404). Derive it at runtime
// from the document's own directory so it's correct wherever the bundle is deployed.
const basepath = new URL(".", document.baseURI).pathname.replace(/\/$/, "") || "/";

export const router = createRouter({ routeTree: rootRoute, basepath, defaultPreload: false });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
