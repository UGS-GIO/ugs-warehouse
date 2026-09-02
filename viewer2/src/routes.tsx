// Path-based route tree. viewer/ uses ONE root route with `?view=` because it is served from a GCS
// bucket that cannot rewrite an arbitrary path to index.html — a real path 404s on reload there.
// viewer2 targets Firebase Hosting, whose SPA rewrite (`** -> /index.html`, firebase.json) serves
// index.html for any path, so views can be real routes: /map, /discover, /catalog, ….
//
// Search params stay for what is genuinely *state about a view*, not the view itself:
//   c/i  selected collection / item      l  active layer ids      s  series selection
// Any other param (catalog, m, ftsdb, vssdb, models, extrepo, features, searchCorpus, and the
// Discover filter keys) passes through untouched so override and deep links keep working.
import { createRootRoute, createRoute, createRouter, type RouteComponent } from "@tanstack/react-router";

import { AppLayout } from "./app";
import { ArchRoute, DevelopersRoute, GuideRoute, ReviewRoute, SearchRoute } from "./routes/simple";
import { CatalogRoute, DiscoverRoute, LandingRoute, MapRoute, PreviewRoute } from "./routes/views";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

/** Selection state shared by every view; unknown params pass through untouched. */
const validateSearch = (s: Record<string, unknown>) => ({
  ...s,
  c: str(s.c),
  i: str(s.i),
  l: str(s.l),
  s: str(s.s),
});

const rootRoute = createRootRoute({ component: AppLayout, validateSearch });

const route = (path: string, component: RouteComponent) =>
  createRoute({ getParentRoute: () => rootRoute, path, component });

// "/" is the landing page — the param-less front door, now a real route rather than the absence
// of a search param.
const routeTree = rootRoute.addChildren([
  route("/", LandingRoute),
  route("/catalog", CatalogRoute),
  route("/map", MapRoute),
  route("/discover", DiscoverRoute),
  route("/search", SearchRoute),
  route("/arch", ArchRoute),
  route("/guide", GuideRoute),
  route("/developers", DevelopersRoute),
  route("/preview", PreviewRoute),
  route("/review", ReviewRoute),
]);

export const router = createRouter({ routeTree, defaultPreload: false });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
