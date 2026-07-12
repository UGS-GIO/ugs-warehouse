// Review-comments client — talks to the IAP serving app's /api/comments (same-origin, the IAP session
// cookie rides along). Review deploy only; the public deploy has no such API.
export type Comment = {
  id: number;
  item_ids: string[];
  target_kind: "item" | "row" | "column";
  feature_ids: number[] | null;  // set when target_kind = row (1..N features)
  column_name: string | null;    // set when target_kind = column
  parent_id: number | null;      // set on a reply → its thread root; null = top-level
  body: string;
  author: string;      // IAP email
  status: string;      // open | resolved
  created_at: string;
  updated_at: string;
};

// What a comment is attached to: the whole item, feature(s)/row(s), or a column.
// featureId = a single feature (lists that feature's thread). featureIds = create on many (multi-select).
export type CommentTarget = {
  kind?: "item" | "row" | "column";
  featureId?: number;      // list/single-target
  featureIds?: number[];   // create on N features (multi-select)
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

// Comments for an item, optionally narrowed to a feature (row) or column.
export const listComments = (itemId: string, target?: { featureId?: number; column?: string }) => {
  const q = new URLSearchParams({ item_id: itemId });
  if (target?.featureId != null) q.set("feature_id", String(target.featureId));
  if (target?.column) q.set("column", target.column);
  return api<Comment[]>(`/api/comments?${q.toString()}`);
};

// All comments across the catalog (Review dashboard), optionally filtered by status.
export const listAllComments = (status?: string) =>
  api<Comment[]>(`/api/comments${status ? `?status=${encodeURIComponent(status)}` : ""}`);

export const createComment = (itemIds: string[], body: string, target?: CommentTarget) => {
  // create on the explicit multi-select set, else the single featureId, else none.
  const featureIds = target?.featureIds ?? (target?.featureId != null ? [target.featureId] : null);
  return api<Comment>(`/api/comments`, {
    method: "POST",
    body: JSON.stringify({
      item_ids: itemIds, body,
      target_kind: target?.kind ?? "item",
      feature_ids: featureIds,
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

// ---- Per-layer review status ----
export type ItemStatus = { item_id: string; status: string; updated_by: string; updated_at: string };
// The review lifecycle; `approved` = ready to promote review → current.
export const ITEM_STATUSES = ["pending", "in_review", "changes_requested", "approved"];

export const listItemStatuses = () => api<ItemStatus[]>(`/api/item-status`);
export const setItemStatus = (itemId: string, status: string) =>
  api<ItemStatus>(`/api/item-status/${encodeURIComponent(itemId)}`, { method: "PUT", body: JSON.stringify({ status }) });
