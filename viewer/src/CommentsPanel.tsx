import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type Comment, type CommentTarget, createComment, deleteComment, listComments, setStatus, whoami } from "./comments";

// Review comments for a target (whole item, a feature/row, or a column) — list + add + resolve/delete.
// Review deploy only (callers gate on IS_REVIEW), same-origin behind IAP so the author is the reviewer.
export function CommentsPanel({ itemId, target, label = "Review comments" }: {
  itemId: string; target?: CommentTarget; label?: string;
}) {
  const qc = useQueryClient();
  // Multi-select (comment on N features at once) has no single thread to show — compose only.
  const composeOnly = (target?.featureIds?.length ?? 0) > 1;
  const key = ["comments", itemId, target?.kind ?? "item", target?.featureId ?? null, target?.column ?? null];
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: ["comments-all"] });  // keep the Review dashboard fresh
  };

  const { data: comments = [], isLoading, error } = useQuery({
    queryKey: key,
    queryFn: () => listComments(itemId, { featureId: target?.featureId, column: target?.column }),
    retry: false,
    enabled: !composeOnly,
  });
  const me = useQuery({ queryKey: ["whoami"], queryFn: whoami, retry: false, staleTime: Infinity });
  const myEmail = me.data?.email;

  const [body, setBody] = useState("");
  const add = useMutation({ mutationFn: () => createComment([itemId], body, target), onSuccess: () => { setBody(""); invalidate(); } });
  const toggle = useMutation({ mutationFn: (c: Comment) => setStatus(c.id, c.status === "resolved" ? "open" : "resolved"), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id: number) => deleteComment(id), onSuccess: invalidate });

  // 503 = DB not wired on the deploy; the HTML/JSON-parse errors = no API behind this origin (local dev,
  // or the public build where /api/comments doesn't exist). Both → "backend not available", not a scary error.
  const notConfigured = error &&
    /\b503\b|Unexpected token|not valid JSON|<!doctype/i.test(String(error));

  return (
    <div className="mt-3 rounded-md border border-border bg-card/50 p-2 text-xs">
      <div className="mb-1.5 font-medium">{label}{comments.length ? ` (${comments.length})` : ""}</div>

      {isLoading && <p className="text-muted-foreground">Loading…</p>}
      {error && (
        <p className="text-muted-foreground">
          {notConfigured ? "Comments backend not configured yet." : `Couldn't load comments (${String(error)}).`}
        </p>
      )}
      {!composeOnly && !isLoading && !error && comments.length === 0 && (
        <p className="text-muted-foreground">No comments yet.</p>
      )}

      <ul className="space-y-1.5">
        {comments.map((c) => (
          <li key={c.id} className="rounded border border-border bg-background p-1.5">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="font-medium text-foreground">{c.author.split("@")[0]}</span>
              <span className="text-[10px] text-muted-foreground">{new Date(c.created_at).toLocaleString()}</span>
              {c.status === "resolved" && (
                <span className="rounded-full border border-green-500/40 bg-green-500/10 px-1.5 text-[10px] text-green-600 dark:text-green-400">resolved</span>
              )}
            </div>
            <p className="mt-0.5 whitespace-pre-wrap text-foreground">{c.body}</p>
            <div className="mt-1 flex gap-3 text-[11px]">
              <button className="text-primary hover:underline" disabled={toggle.isPending}
                onClick={() => toggle.mutate(c)}>{c.status === "resolved" ? "reopen" : "resolve"}</button>
              {myEmail === c.author && (
                <button className="text-destructive hover:underline" disabled={remove.isPending}
                  onClick={() => remove.mutate(c.id)}>delete</button>
              )}
            </div>
          </li>
        ))}
      </ul>

      {!notConfigured && (
        <div className="mt-2 flex gap-1.5">
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={2}
            placeholder="Add a review note…"
            className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs" />
          <button disabled={!body.trim() || add.isPending} onClick={() => add.mutate()}
            className="self-end rounded border border-border bg-primary px-2 py-1 text-primary-foreground hover:opacity-90 disabled:opacity-60">
            {add.isPending ? "…" : "Add"}
          </button>
        </div>
      )}
      {add.error && <p className="mt-1 text-destructive">Failed to add: {String(add.error)}</p>}
    </div>
  );
}
