// The layout route. Search params that are STATE ABOUT A VIEW rather than the view itself:
//   c/i  selected collection / item      l  active layer ids      s  series selection
//   sheet  the phone map sheet (map-model parseSheet)      terrain  the item preview's 3D terrain
//   cube  each datacube's variable + steps (zarr/cube-picks); rides along between catalog and map
// Any other param (catalog, m, ftsdb, vssdb, models, extrepo, features, and the Discover filter
// keys) passes through untouched so override and deep links keep working.
import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Link } from "@tanstack/react-router";

import { AppLayout } from "@/app";

// The index signature is the passthrough contract, and it is what lets `useSearch` stay typed
// without a cast: the four known keys are narrowed, everything else survives as unknown.
export type ViewerSearch = {
  c?: string;
  i?: string;
  l?: string;
  s?: string;
  sheet?: string;
  terrain?: true;
  cube?: string;
  [key: string]: unknown;
};

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

// Without this an unknown path falls to the router's default bare "Not Found" text, outside the
// app shell — the dev server warns about it on every miss.
function NotFound() {
  return (
    <div className="mx-auto max-w-2xl px-6 py-16 text-center">
      <h1 className="font-display text-2xl tracking-tight">Page not found</h1>
      <p className="mt-2 text-muted-foreground">That URL doesn&rsquo;t match anything in the viewer.</p>
      <Link to="/" className="mt-6 inline-block text-primary hover:underline">Back to the catalog</Link>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: AppLayout,
  notFoundComponent: NotFound,
  validateSearch: (s: Record<string, unknown>): ViewerSearch => ({
    ...s,
    c: str(s.c),
    i: str(s.i),
    l: str(s.l),
    s: str(s.s),
    sheet: str(s.sheet),
    cube: str(s.cube),
    terrain: s.terrain === true || s.terrain === "true" ? true : undefined,
  }),
});
