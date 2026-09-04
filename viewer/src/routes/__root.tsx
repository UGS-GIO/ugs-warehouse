// The layout route. Search params that are STATE ABOUT A VIEW rather than the view itself:
//   c/i  selected collection / item      l  active layer ids      s  series selection
// Any other param (catalog, m, ftsdb, vssdb, models, extrepo, features, and the Discover filter
// keys) passes through untouched so override and deep links keep working.
import { createRootRoute } from "@tanstack/react-router";

import { AppLayout } from "../app";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

export const Route = createRootRoute({
  component: AppLayout,
  validateSearch: (s: Record<string, unknown>) => ({
    ...s,
    c: str(s.c),
    i: str(s.i),
    l: str(s.l),
    s: str(s.s),
  }),
});
