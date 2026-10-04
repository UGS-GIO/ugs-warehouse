import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { qk } from "@/query-keys";
import { useRef, useState } from "react";
import { type Comment, type CommentTarget, createComment, deleteComment, listComments, listReviewers, replyToComment, setStatus, whoami } from "./comments";

// The active "@token" immediately left of the caret (the fragment being typed), or null if none.
// `at` is the index of the '@'; `query` is what follows it — what we filter the roster by.
function mentionAt(text: string, caret: number): { at: number; query: string } | null {
  const m = /(?:^|\s)@(\S*)$/.exec(text.slice(0, caret));
  if (!m) return null;
  return { at: caret - m[1].length - 1, query: m[1] };
}

// Review comments for a target (whole item, a feature/row, or a column) — list + add + resolve/delete.
// Review deploy only (callers gate on IS_REVIEW), same-origin behind IAP so the author is the reviewer.
export function CommentsPanel({ itemId, target, label = "Review comments" }: {
  itemId: string; target?: CommentTarget; label?: string;
}) {
  const qc = useQueryClient();
  // Multi-select (comment on N rows at once) has no single thread to show — compose only.
  const composeOnly = (target?.rowVals?.length ?? 0) > 1;
  const key = qk.comments.thread(itemId, target);
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: key });
    qc.invalidateQueries({ queryKey: qk.comments.all });  // keep the Review dashboard fresh
  };

  const { data: comments = [], isLoading, error } = useQuery({
    queryKey: key,
    queryFn: () => listComments(itemId, { rowVal: target?.rowVal, column: target?.column }),
    retry: false,
    enabled: !composeOnly,
  });
  const me = useQuery({ queryKey: qk.whoami, queryFn: whoami, retry: false, staleTime: Infinity });
  const myEmail = me.data?.email;

  const [body, setBody] = useState("");
  // @-mention autocomplete: roster = review group members (fetched once, filtered client-side).
  const reviewers = useQuery({ queryKey: qk.reviewers, queryFn: listReviewers, retry: false });
  const taRef = useRef<HTMLTextAreaElement>(null);
  const [mentions, setMentions] = useState<string[]>([]);  // current dropdown matches (empty = hidden)
  const [mentionIdx, setMentionIdx] = useState(0);         // keyboard-highlighted row in the dropdown

  const onBodyChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    setBody(v);
    const tok = mentionAt(v, e.target.selectionStart ?? v.length);
    if (!tok || !reviewers.data?.length) return setMentions([]);
    const q = tok.query.toLowerCase();
    setMentions(reviewers.data.filter((r) => r.toLowerCase().includes(q)).slice(0, 6));
    setMentionIdx(0);  // reset the highlight to the top match whenever the list changes
  };

  // Keyboard-drive the dropdown: ↑/↓ move the highlight, Enter/Tab pick it, Esc dismisses. Only
  // when the dropdown is open, so a normal textarea (newline on Enter, etc.) is untouched otherwise.
  const onBodyKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentions.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setMentionIdx((i) => (i + 1) % mentions.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setMentionIdx((i) => (i - 1 + mentions.length) % mentions.length); }
    else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickMention(mentions[mentionIdx]); }
    else if (e.key === "Escape") { e.preventDefault(); setMentions([]); }
  };

  const pickMention = (email: string) => {
    const ta = taRef.current;
    const caret = ta?.selectionStart ?? body.length;
    const tok = mentionAt(body, caret);
    if (!tok) return;
    const local = email.split("@")[0];  // display as @local — one group, one domain, so it's unambiguous
    const next = `${body.slice(0, tok.at)}@${local} ${body.slice(caret)}`;
    setBody(next);
    setMentions([]);
    const pos = tok.at + local.length + 2;  // just past the inserted "@local "
    requestAnimationFrame(() => { ta?.focus(); ta?.setSelectionRange(pos, pos); });
  };

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
        <div className="relative mt-2 flex gap-1.5">
          {mentions.length > 0 && (
            <ul className="absolute bottom-full left-0 z-10 mb-1 max-h-40 w-56 overflow-auto rounded border border-border bg-card shadow">
              {mentions.map((email, i) => (
                <li key={email}>
                  <button type="button" onMouseDown={(e) => { e.preventDefault(); pickMention(email); }}
                    onMouseEnter={() => setMentionIdx(i)}
                    className={`block w-full px-2 py-1 text-left ${i === mentionIdx ? "bg-muted" : ""}`}>
                    <span className="font-medium text-foreground">@{email.split("@")[0]}</span>
                    <span className="ml-1 text-xs text-muted-foreground">{email}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <textarea ref={taRef} value={body} onChange={onBodyChange} onKeyDown={onBodyKeyDown} rows={2}
            onBlur={() => setMentions([])}
            placeholder="Add a review note… (@ to mention)"
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
          Saved ✓{composeOnly && target?.rowVals?.length ? ` — added to ${target.rowVals.length} rows` : ""}
        </p>
      )}
    </div>
  );
}

// Render a comment body with @mentions styled (UCRC-style). Split on the @token so the rest stays
// plain text — we emit React spans, never HTML, so there's no injection surface.
function renderBody(body: string) {
  return body.split(/(?<!\S)(@[A-Za-z0-9][A-Za-z0-9._%+-]*)/g).map((part, i) =>
    /^@[A-Za-z0-9]/.test(part)
      ? <span key={i} className="rounded bg-primary/10 px-0.5 font-medium text-primary">{part}</span>
      : part,
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
        <span className="text-xs text-muted-foreground">{new Date(c.created_at).toLocaleString()}</span>
        {c.status === "resolved" && (
          <span className="rounded-full border border-green-500/40 bg-green-500/10 px-1.5 text-xs text-green-600 dark:text-green-400">resolved</span>
        )}
      </div>
      <p className="mt-0.5 whitespace-pre-wrap text-foreground">{renderBody(c.body)}</p>
      <div className="mt-1 flex gap-3 text-xs">
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
  if (!open) return <button className="mt-1 text-xs text-primary hover:underline" onClick={() => setOpen(true)}>Reply</button>;
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
