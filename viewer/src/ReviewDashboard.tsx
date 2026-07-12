import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type Comment, deleteComment, listAllComments, setStatus, whoami } from "./comments";

// Review dashboard — every comment across the catalog, filterable by status, resolve/reopen/delete inline.
// A "Review" tab (App.tsx) renders this on the review deploy only. onOpen jumps to the item's catalog page.
export function ReviewDashboard({ onOpen }: { onOpen: (itemId: string) => void }) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<"open" | "all" | "resolved">("open");
  const invalidate = () => qc.invalidateQueries({ queryKey: ["comments-all"] });

  const { data: comments = [], isLoading, error } = useQuery({
    queryKey: ["comments-all", filter],
    queryFn: () => listAllComments(filter === "all" ? undefined : filter),
    retry: false,
  });
  const me = useQuery({ queryKey: ["whoami"], queryFn: whoami, retry: false, staleTime: Infinity });
  const toggle = useMutation({ mutationFn: (c: Comment) => setStatus(c.id, c.status === "resolved" ? "open" : "resolved"), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id: number) => deleteComment(id), onSuccess: invalidate });

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
      </div>

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
