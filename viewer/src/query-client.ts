import { QueryClient } from "@tanstack/react-query";

/** One client, shared by the app and the router context (so a loader can prefetch into it).
 *
 *  Catalog JSON, styles and parquet pages are immutable per ingest, so five minutes of freshness
 *  costs nothing and stops a remount refetching what it just read. `retry: 1` because the CDN either
 *  has the object or it does not. Window-focus refetching stays on, so a review thread that has gone
 *  stale comes back current. */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5 * 60_000, retry: 1 } },
});
