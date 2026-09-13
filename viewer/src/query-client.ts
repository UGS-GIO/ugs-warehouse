import { QueryClient } from "@tanstack/react-query";

/** One client, shared by the app and the router context (so a loader can prefetch into it).
 *
 *  Defaults, where every call site used to repeat them: catalog JSON, styles and parquet pages are
 *  immutable per ingest, so five minutes of freshness costs nothing and stops a remount refetching
 *  what it just read. `retry: 1` because the CDN either has the object or it does not — three
 *  backed-off retries only slow a 404 down. Window-focus refetching stays on: after five minutes
 *  away, a review comment thread should come back current. */
export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5 * 60_000, retry: 1 } },
});
