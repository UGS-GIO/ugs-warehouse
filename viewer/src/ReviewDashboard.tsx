import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type Comment, deleteComment, ITEM_STATUSES, listAllComments, setStatus, whoami } from "./comments";
import { ReviewReport } from "./ReviewReport";
import { statusClass, statusLabel, useItemStatuses } from "./ReviewStatus";

// Review dashboard — every comment across the catalog, filterable by status, resolve/reopen/delete inline.
// A "Review" tab (App.tsx) renders this on the review deploy only. onOpen jumps to the item's catalog page.
export function ReviewDashboard({ onOpen }: { onOpen: (itemId: string) => void }) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"open" | "all" | "resolved">("open");
  const [report, setReport] = useState(false);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["comments-all"] });

  // Fetch everything (roots + replies) and thread client-side so the status filter never orphans a
  // reply from its root. Filtering by status applies to the thread ROOT.
  const { data: all = [], isLoading, error } = useQuery({
    queryKey: ["comments-all"],
    queryFn: () => listAllComments(),
    retry: false,
  });
  const me = useQuery({ queryKey: ["whoami"], queryFn: whoami, retry: false, staleTime: Infinity });
  const toggle = useMutation({ mutationFn: (c: Comment) => setStatus(c.id, c.status === "resolved" ? "open" : "resolved"), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id: number) => deleteComment(id), onSuccess: invalidate });

  const repliesByRoot = new Map<number, Comment[]>();
  for (const c of all) if (c.parent_id != null) (repliesByRoot.get(c.parent_id) ?? repliesByRoot.set(c.parent_id, []).get(c.parent_id)!).push(c);
  const comments = all.filter((c) => c.parent_id == null &&
    (filter === "all" || (filter === "resolved" ? c.status === "resolved" : c.status !== "resolved")));

  const notConfigured = error && /\b503\b/.test(String(error));

  return (
    <div className="mx-auto max-w-3xl p-4">
      <h2 className="text-lg font-semibold">Review dashboard</h2>
      <p className="mt-0.5 text-sm text-muted-foreground">Every review comment across the catalog.</p>

      <div className="mt-3 flex gap-1.5 text-xs">
        {(["open", "all", "resolved"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`rounded border px-2 py-0.5 capitalize ${filter === f
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border bg-card text-foreground hover:bg-accent"}`}>{f}</button>
        ))}
        {!isLoading && !error && <span className="self-center text-muted-foreground">· {comments.length} shown</span>}
        <button onClick={() => setReport(true)}
          className="ml-auto rounded border border-border bg-card px-2 py-0.5 text-foreground hover:border-primary">
          Report / export ↧
        </button>
      </div>

      {report && <ReviewReport onClose={() => setReport(false)} onOpen={(id) => { setReport(false); onOpen(id); }} />}

      <LayerStatusSummary onOpen={onOpen} />

      {isLoading && <p className="mt-3 text-sm text-muted-foreground">Loading…</p>}
      {error && (
        <p className="mt-3 text-sm text-muted-foreground">
          {notConfigured ? "Comments backend not configured yet." : `Couldn't load comments (${String(error)}).`}
        </p>
      )}
      {!isLoading && !error && comments.length === 0 && (
        <p className="mt-3 text-sm text-muted-foreground">No {filter === "all" ? "" : filter} comments.</p>
      )}

      <ul className="mt-3 divide-y divide-border">
        {comments.map((c) => (
          <li key={c.id} className="py-2 text-sm">
            <div className="flex flex-wrap items-baseline gap-2">
              {c.item_ids.map((it) => (
                <button key={it} onClick={() => onOpen(it)}
                  className="rounded bg-muted px-1.5 font-mono text-[11px] text-primary hover:underline">{it}</button>
              ))}
              <span className="font-medium text-foreground">{c.author.split("@")[0]}</span>
              <span className="text-[11px] text-muted-foreground">{new Date(c.created_at).toLocaleString()}</span>
              {c.status === "resolved" && (
                <span className="rounded-full border border-green-500/40 bg-green-500/10 px-1.5 text-[10px] text-green-600 dark:text-green-400">resolved</span>
              )}
            </div>
            <p className="mt-0.5 whitespace-pre-wrap text-foreground">{c.body}</p>
            {(repliesByRoot.get(c.id) ?? []).map((r) => (
              <div key={r.id} className="mt-1 border-l-2 border-border pl-2 text-[13px]">
                <span className="font-medium text-foreground">{r.author.split("@")[0]}</span>
                <span className="ml-2 text-[11px] text-muted-foreground">{new Date(r.created_at).toLocaleString()}</span>
                <p className="whitespace-pre-wrap text-foreground">{r.body}</p>
              </div>
            ))}
            <div className="mt-1 flex gap-3 text-[11px]">
              <button className="text-primary hover:underline" disabled={toggle.isPending}
                onClick={() => toggle.mutate(c)}>{c.status === "resolved" ? "reopen" : "resolve"}</button>
              {me.data?.email === c.author && (
                <button className="text-destructive hover:underline" disabled={remove.isPending}
                  onClick={() => remove.mutate(c.id)}>delete</button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Layer review-completion overview — counts per status + the layers that have been touched (non-pending).
function LayerStatusSummary({ onOpen }: { onOpen: (itemId: string) => void }) {
  const { data: statuses = [], error } = useItemStatuses();
  if (error) return null;  // backend not wired / no statuses yet
  const counts = Object.fromEntries(ITEM_STATUSES.map((s) => [s, 0])) as Record<string, number>;
  for (const s of statuses) counts[s.status] = (counts[s.status] ?? 0) + 1;
  const tracked = statuses.filter((s) => s.status !== "pending");

  return (
    <section className="mt-3 rounded-md border border-border bg-card/50 p-3">
      <h3 className="text-sm font-semibold">Layer review status</h3>
      <div className="mt-1.5 flex flex-wrap gap-1.5 text-xs">
        {ITEM_STATUSES.map((s) => (
          <span key={s} className={`rounded-full border px-2 py-0.5 ${statusClass(s)}`}>
            {counts[s]} {statusLabel(s).toLowerCase()}
          </span>
        ))}
      </div>
      {tracked.length > 0 && (
        <ul className="mt-2 divide-y divide-border text-sm">
          {tracked.map((s) => (
            <li key={s.item_id} className="flex flex-wrap items-center gap-2 py-1">
              <button onClick={() => onOpen(s.item_id)}
                className="rounded bg-muted px-1.5 font-mono text-[11px] text-primary hover:underline">{s.item_id}</button>
              <span className={`rounded-full border px-1.5 text-[10px] ${statusClass(s.status)}`}>{statusLabel(s.status)}</span>
              <span className="text-[11px] text-muted-foreground">{s.updated_by.split("@")[0]} · {new Date(s.updated_at).toLocaleDateString()}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
