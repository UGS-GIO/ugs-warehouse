// Router instance over the GENERATED tree (routeTree.gen.ts, built from src/routes/ by
// @tanstack/router-plugin — see vite.config.ts). Nothing here is hand-maintained but the basepath.
import { createRouter } from "@tanstack/react-router";

import { toBasepath } from "./lib/mount";
import { queryClient } from "./query-client";
import { routeTree } from "./routeTree.gen";

// Path routes have to know where the bundle is mounted or they'd read the mount prefix as part of
// the route. Vite's `base` is that prefix (see mount.ts).
export const router = createRouter({
  routeTree,
  basepath: toBasepath(import.meta.env.BASE_URL),
  // The query client rides the context so a route loader can prefetch into the cache the components
  // read. "intent" warms a route's code-split chunk on hover/touch-start.
  context: { queryClient },
  defaultPreload: "intent",
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
