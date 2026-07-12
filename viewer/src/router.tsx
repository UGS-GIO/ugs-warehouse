// TanStack Router setup. The viewer is a static SPA on GCS (served at /warehouse/viewer/ with no
// server-side rewrites), so we use ONE root route + typed search params rather than path routes —
// every URL stays /warehouse/viewer/?…, which always resolves to index.html (no 404 on reload/deep
// link), while the router gives us typed search, history, and clean per-view navigation.
import { createBrowserHistory, createRootRoute, createRouter } from "@tanstack/react-router";

import { App } from "./App";

const NAV_VIEWS = ["map", "search", "arch", "guide", "review"] as const;  // "catalog" is the param-less default
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

// The viewer is a static SPA fronted by a Cloud LB backend bucket, which does NOT serve index.html
// for a bare directory request (`/warehouse/viewer/` → NoSuchKey). So keep the actual served
// filename in the path: use the document's OWN pathname (…/index.html in prod) as the basepath.
// Navigation + reloads then stay on `/warehouse/viewer/index.html?…`, always a real object → no 404,
// no bucket/LB change needed. In dev the path is `/`, so this is a no-op there.
const basepath = location.pathname.replace(/\/$/, "") || "/";

// TanStack renders the single root route as `basepath + "/"` — i.e. `…/index.html/?…`. That trailing
// slash is a DIFFERENT, nonexistent GCS object key (`…/index.html/` → NoSuchKey), so reloads 404.
// `trailingSlash` doesn't govern the basepath boundary, so strip that one slash in `createHref` (the
// fn that builds the address-bar URL). Result stays `…/index.html?…` — a real object. The router's
// internal matching is unaffected (it parses the original href; we only read search params).
const stripBoundarySlash = (href: string) =>
  href.startsWith(`${basepath}/`) ? basepath + href.slice(basepath.length + 1) : href;
const history = createBrowserHistory({ createHref: stripBoundarySlash });

export const router = createRouter({ routeTree: rootRoute, basepath, history, defaultPreload: false });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
