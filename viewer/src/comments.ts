// Review-comments client — talks to the IAP serving app's /api/comments (same-origin, the IAP session
// cookie rides along). Review deploy only; the public deploy has no such API.
export type Comment = {
  id: number;
  item_ids: string[];
  body: string;
  author: string;      // IAP email
  status: string;      // open | resolved
  created_at: string;
  updated_at: string;
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

export const listComments = (itemId: string) =>
  api<Comment[]>(`/api/comments?item_id=${encodeURIComponent(itemId)}`);

export const createComment = (itemIds: string[], body: string) =>
  api<Comment>(`/api/comments`, { method: "POST", body: JSON.stringify({ item_ids: itemIds, body }) });

export const setStatus = (id: number, status: string) =>
  api<Comment>(`/api/comments/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });

export const deleteComment = (id: number) =>
  api<{ deleted: number }>(`/api/comments/${id}`, { method: "DELETE" });

export const whoami = () => api<{ email: string; user: string }>(`/whoami`);
