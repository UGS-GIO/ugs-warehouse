// What the UI says when it has no connection: a badge in the header, and a plain message where a
// panel needs the network, instead of a spinner that never ends or a raw "Failed to fetch".
import { isNetworkError, useOnline } from "./online";

export function OfflineBadge() {
  if (useOnline()) return null;
  return (
    <span role="status" title="No connection: showing what is saved on this device"
      className="rounded-full border border-border bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
      Offline
    </span>
  );
}

/**
 * Stand-in for a panel that could not load. Offline — the browser says so, or the request failed
 * at the network — it says what is unavailable; otherwise the fallback, or a plain "could not load".
 */
export function Unavailable({ what, error, fallback }: { what: string; error?: unknown; fallback?: string }) {
  const offline = !useOnline() || isNetworkError(error);
  return (
    <p className="text-sm text-muted-foreground">
      {offline
        ? `${what[0].toUpperCase()}${what.slice(1)} is not available offline.`
        : (fallback ?? `Could not load ${what}.`)}
    </p>
  );
}
