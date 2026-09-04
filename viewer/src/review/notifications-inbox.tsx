import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type Notification, listNotifications, markNotificationsSeen } from "./comments";

// Review deploy only (callers gate on IS_REVIEW). Both the inbox and the nav bell read the same
// ["notifications"] query, so they share one fetch + one cache. 503 / no-API (public build, local
// dev) → treated as "not configured" and both render nothing.
const useNotifications = () =>
  useQuery({
    queryKey: ["notifications"],
    queryFn: () => listNotifications(),
    retry: false,
    refetchInterval: 60_000,  // gentle poll so mentions/replies show up without a manual refresh
  });

const isNotConfigured = (error: unknown) =>
  !!error && /\b503\b|Unexpected token|not valid JSON|<!doctype/i.test(String(error));

// "clinton mentioned you on wells_spatial" — actor + what they did + where.
function label(n: Notification): string {
  const who = n.actor.split("@")[0];
  const verb = n.kind === "mention" ? "mentioned you" : "replied in a thread you're in";
  const where =
    n.target_kind === "column" ? `column "${n.column_name}"` :
    n.target_kind === "row" ? "a feature" :
    (n.item_ids?.[0] ?? "an item");
  return `${who} ${verb} on ${where}`;
}

// A bell + unread count for the app nav. Click → the review view (where the inbox lives). Styled as a
// button matching the theme toggle so it sits centered in the nav, with the unread count beside it.
export function NotifBell({ onClick }: { onClick: () => void }) {
  const { data = [], error } = useNotifications();
  if (isNotConfigured(error)) return null;
  const unread = data.filter((n) => !n.seen_at).length;
  return (
    <button onClick={onClick} aria-label={`Notifications${unread ? ` — ${unread} unread` : ""}`}
      title={`Notifications${unread ? ` — ${unread} unread` : ""}`}
      className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1.5 text-sm text-foreground hover:bg-accent">
      <span aria-hidden>🔔</span>
      {unread > 0 && (
        <span className="min-w-4.5 rounded-full bg-primary px-1 text-center text-xs font-semibold leading-tight text-primary-foreground">
          {unread}
        </span>
      )}
    </button>
  );
}

// The inbox, rendered at the top of the Review dashboard. Clicking a notification marks it read and
// jumps to the item it's about.
export function NotificationsInbox({ onOpen }: { onOpen: (itemId: string) => void }) {
  const qc = useQueryClient();
  const { data: items = [], isLoading, error } = useNotifications();
  const seen = useMutation({
    mutationFn: (ids?: number[]) => markNotificationsSeen(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  if (isNotConfigured(error)) return null;
  if (!isLoading && items.length === 0) return null;  // nothing to show → no empty box
  const unread = items.filter((n) => !n.seen_at).length;

  return (
    <div className="mb-4 rounded-md border border-border bg-card/50 p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">🔔 Notifications{unread ? ` (${unread} unread)` : ""}</h3>
        {unread > 0 && (
          <button className="text-xs text-primary hover:underline" disabled={seen.isPending}
            onClick={() => seen.mutate(undefined)}>Mark all read</button>
        )}
      </div>
      <ul className="mt-2 space-y-1">
        {items.map((n) => (
          <li key={n.id}>
            <button
              onClick={() => { if (!n.seen_at) seen.mutate([n.id]); if (n.item_ids?.[0]) onOpen(n.item_ids[0]); }}
              className={`block w-full rounded px-2 py-1 text-left text-xs hover:bg-muted ${n.seen_at ? "opacity-60" : "font-medium"}`}>
              <span className="text-foreground">{label(n)}</span>
              <span className="ml-1 text-muted-foreground">— {n.body.slice(0, 80)}{n.body.length > 80 ? "…" : ""}</span>
              <span className="ml-1 whitespace-nowrap text-xs text-muted-foreground">{new Date(n.created_at).toLocaleString()}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
