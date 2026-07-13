import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type Comment, type CommentTarget, createComment, deleteComment, listComments, replyToComment, setStatus, whoami } from "./comments";

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
  const add = useMutation({
    mutationFn: () => createComment([itemId], body, target),
    onSuccess: (created) => {
      setBody("");
      // Optimistically show it now — refetch alone leaves no cue, and compose-only (multi-select)
      // has the list query disabled so invalidate would never surface it.
      if (!composeOnly) qc.setQueryData<Comment[]>(key, (old = []) => [created, ...old]);
      invalidate();
    },
  });
  const toggle = useMutation({ mutationFn: (c: Comment) => setStatus(c.id, c.status === "resolved" ? "open" : "resolved"), onSuccess: invalidate });
  const remove = useMutation({ mutationFn: (id: number) => deleteComment(id), onSuccess: invalidate });
  const reply = useMutation({ mutationFn: (v: { parentId: number; body: string }) => replyToComment(v.parentId, v.body), onSuccess: invalidate });

  // Thread the flat list: top-level comments (parent_id null) each with their replies, oldest-first.
  const roots = comments.filter((c) => c.parent_id == null);
  const repliesByRoot = new Map<number, Comment[]>();
  for (const c of comments) if (c.parent_id != null) (repliesByRoot.get(c.parent_id) ?? repliesByRoot.set(c.parent_id, []).get(c.parent_id)!).push(c);

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
      {!composeOnly && !isLoading && !error && roots.length === 0 && (
        <p className="text-muted-foreground">No comments yet.</p>
      )}

      <ul className="space-y-1.5">
        {roots.map((root) => (
          <li key={root.id} className="rounded border border-border bg-background p-1.5">
            <CommentRow c={root} myEmail={myEmail}
              onToggle={() => toggle.mutate(root)} toggling={toggle.isPending}
              onDelete={() => remove.mutate(root.id)} deleting={remove.isPending} />
            {(repliesByRoot.get(root.id) ?? []).map((r) => (
              <div key={r.id} className="mt-1.5 border-l-2 border-border pl-2">
                <CommentRow c={r} myEmail={myEmail}
                  onDelete={() => remove.mutate(r.id)} deleting={remove.isPending} />
              </div>
            ))}
            {!notConfigured && <ReplyBox onReply={(text) => reply.mutate({ parentId: root.id, body: text })} pending={reply.isPending} />}
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
      {add.isSuccess && !add.isPending && (
        <p key={add.submittedAt} className="save-cue mt-1 text-green-600 dark:text-green-400">
          Saved ✓{composeOnly && target?.featureIds?.length ? ` — added to ${target.featureIds.length} features` : ""}
        </p>
      )}
    </div>
  );
}

// One comment (a thread root or a reply). Resolve is root-only (no onToggle for replies).
function CommentRow({ c, myEmail, onToggle, toggling, onDelete, deleting }: {
  c: Comment; myEmail?: string;
  onToggle?: () => void; toggling?: boolean; onDelete: () => void; deleting?: boolean;
}) {
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-medium text-foreground">{c.author.split("@")[0]}</span>
        <span className="text-[10px] text-muted-foreground">{new Date(c.created_at).toLocaleString()}</span>
        {c.status === "resolved" && (
          <span className="rounded-full border border-green-500/40 bg-green-500/10 px-1.5 text-[10px] text-green-600 dark:text-green-400">resolved</span>
        )}
      </div>
      <p className="mt-0.5 whitespace-pre-wrap text-foreground">{c.body}</p>
      <div className="mt-1 flex gap-3 text-[11px]">
        {onToggle && (
          <button className="text-primary hover:underline" disabled={toggling}
            onClick={onToggle}>{c.status === "resolved" ? "reopen" : "resolve"}</button>
        )}
        {myEmail === c.author && (
          <button className="text-destructive hover:underline" disabled={deleting} onClick={onDelete}>delete</button>
        )}
      </div>
    </>
  );
}

// Collapsed "Reply" link that expands to a compact composer — keeps threads tidy.
function ReplyBox({ onReply, pending }: { onReply: (body: string) => void; pending?: boolean }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  if (!open) return <button className="mt-1 text-[11px] text-primary hover:underline" onClick={() => setOpen(true)}>Reply</button>;
  return (
    <div className="mt-1 flex gap-1.5">
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={1} autoFocus
        placeholder="Reply…" className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs" />
      <button disabled={!text.trim() || pending}
        onClick={() => { onReply(text.trim()); setText(""); setOpen(false); }}
        className="self-end rounded border border-border bg-primary px-2 py-1 text-primary-foreground hover:opacity-90 disabled:opacity-60">
        {pending ? "…" : "Reply"}
      </button>
    </div>
  );
}
