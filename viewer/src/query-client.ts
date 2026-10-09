import { onlineManager, QueryClient } from "@tanstack/react-query";

/** One client, shared by the app and the router context (so a loader can prefetch into it).
 *
 *  Catalog JSON, styles and parquet pages are immutable per ingest, so five minutes of freshness
 *  costs nothing and stops a remount refetching what it just read. `retry: 1` because the CDN either
 *  has the object or it does not. Window-focus refetching stays on, so a review thread that has gone
 *  stale comes back current. */
// networkMode "offlineFirst": try the request even when the browser reports no connection, because
// the service worker may answer it from cache, and only then pause. The default ("online") pauses
// first, so a page opened or refreshed after the connection drops would sit on a spinner forever
// even for catalog JSON already stored on this device.
// onlineManager starts out "online" and only learns otherwise from an offline event, which never
// fires for an app opened with no connection; start it from what the browser says now.
if (typeof navigator !== "undefined") onlineManager.setOnline(navigator.onLine !== false);

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 5 * 60_000, retry: 1, networkMode: "offlineFirst" } },
});
