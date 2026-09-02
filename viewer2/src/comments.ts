// Review-comments client — talks to the IAP serving app's /api/comments (same-origin, the IAP session
// cookie rides along). Review deploy only; the public deploy has no such API.
export type Comment = {
  id: number;
  item_ids: string[];
  target_kind: "item" | "row" | "column";
  row_key: string | null;        // the stable-key column name (e.g. 'pk') when target_kind = row
  row_key_vals: string[] | null; // 1..N stable key values
  column_name: string | null;    // set when target_kind = column
  parent_id: number | null;      // set on a reply → its thread root; null = top-level
  body: string;
  author: string;      // reviewer email (IAP or Firebase/Entra)
  status: string;      // open | resolved
  created_at: string;
  updated_at: string;
};

// What a comment is attached to: the whole item, row(s), or a column. Rows key on a STABLE domain key
// (rowKey = the column name, e.g. 'pk') so a comment resolves to the same row across the internal viewer
// and the hazards-review map viewer. rowVal = one row's thread (list); rowVals = create on N rows.
export type CommentTarget = {
  kind?: "item" | "row" | "column";
  rowKey?: string;
  rowVal?: string;
  rowVals?: string[];
  column?: string;
};

async function api<T>(url: string, opts?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    credentials: "include",
    headers: { "content-type": "application/json" },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${(await r.text().catch(() => "")).slice(0, 200)}`);
  return (r.status === 204 ? undefined : await r.json()) as T;
}

// Comments for an item, optionally narrowed to a row (by stable key value) or column.
export const listComments = (itemId: string, target?: { rowVal?: string; column?: string }) => {
  const q = new URLSearchParams({ item_id: itemId });
  if (target?.rowVal != null) q.set("row_val", target.rowVal);
  if (target?.column) q.set("column", target.column);
  return api<Comment[]>(`/api/comments?${q.toString()}`);
};

// All comments across the catalog (Review dashboard), optionally filtered by status.
export const listAllComments = (status?: string) =>
  api<Comment[]>(`/api/comments${status ? `?status=${encodeURIComponent(status)}` : ""}`);

export const createComment = (itemIds: string[], body: string, target?: CommentTarget) => {
  // create on the explicit multi-select set, else the single rowVal, else none.
  const rowVals = target?.rowVals ?? (target?.rowVal != null ? [target.rowVal] : null);
  return api<Comment>(`/api/comments`, {
    method: "POST",
    body: JSON.stringify({
      item_ids: itemIds, body,
      target_kind: target?.kind ?? "item",
      row_key: target?.rowKey ?? null,
      row_key_vals: rowVals,
      column_name: target?.column ?? null,
    }),
  });
};

// Reply to a comment — inherits the parent's target server-side, so only parent_id + body are sent.
export const replyToComment = (parentId: number, body: string) =>
  api<Comment>(`/api/comments`, {
    method: "POST",
    body: JSON.stringify({ parent_id: parentId, body }),
  });

export const setStatus = (id: number, status: string) =>
  api<Comment>(`/api/comments/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });

export const deleteComment = (id: number) =>
  api<{ deleted: number }>(`/api/comments/${id}`, { method: "DELETE" });

export const whoami = () => api<{ email: string; user: string }>(`/whoami`);

// Emails of the review Google Group's members — the set that can be @-mentioned in a comment.
export const listReviewers = () => api<string[]>(`/api/reviewers`);

// ---- In-app notifications ----
// One notification for the signed-in reviewer: someone @mentioned them, or replied in their thread.
// Joined to the triggering comment so the UI can label + link it. `seen_at` null = unread.
export type Notification = {
  id: number;
  actor: string;                 // who triggered it
  kind: "mention" | "reply";
  seen_at: string | null;
  created_at: string;
  comment_id: number;
  body: string;                  // the comment's text (for a preview snippet)
  item_ids: string[];
  target_kind: string;           // item | row | column
  row_key: string | null;
  row_key_vals: string[] | null;
  column_name: string | null;
  parent_id: number | null;
};

export const listNotifications = (unseen = false) =>
  api<Notification[]>(`/api/notifications${unseen ? "?unseen=true" : ""}`);

// Mark mine read: pass ids to mark those, or omit to mark all of mine.
export const markNotificationsSeen = (ids?: number[]) =>
  api<{ ok: boolean }>(`/api/notifications/seen`, {
    method: "POST",
    body: JSON.stringify({ ids: ids ?? null }),
  });

// ---- Per-layer review status ----
export type ItemStatus = { item_id: string; status: string; updated_by: string; updated_at: string };
// The review lifecycle; `approved` = ready to promote review → current.
export const ITEM_STATUSES = ["pending", "in_review", "changes_requested", "approved"];

export const listItemStatuses = () => api<ItemStatus[]>(`/api/item-status`);
export const setItemStatus = (itemId: string, status: string) =>
  api<ItemStatus>(`/api/item-status/${encodeURIComponent(itemId)}`, { method: "PUT", body: JSON.stringify({ status }) });
