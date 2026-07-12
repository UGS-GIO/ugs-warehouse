import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { type Comment, listAllComments } from "./comments";

// Print-styled report of every review comment, grouped by item. "Export PDF" = the browser print
// dialog (Save as PDF); "Email draft" = a mailto: with a text summary. Review deploy only.
// A server-rendered PDF + real email-with-attachment is the v2 (ugs-ingest#190).

// Human label for what a comment targets: whole item, feature row(s), or a column.
function targetLabel(c: Comment): string {
  if (c.target_kind === "row") return `feature ${(c.feature_ids ?? []).join(", ") || "?"}`;
  if (c.target_kind === "column") return `column “${c.column_name ?? "?"}”`;
  return "item";
}

export function ReviewReport({ onClose, onOpen }: { onClose: () => void; onOpen?: (itemId: string) => void }) {
  const { data: comments = [], isLoading, error } = useQuery({
    queryKey: ["comments-all", "report"], queryFn: () => listAllComments(), retry: false,
  });

  // Group by item id (a comment spanning N items appears under each). Sorted: most comments first.
  const groups = useMemo(() => {
    const m = new Map<string, Comment[]>();
    // Group thread ROOTS by item; replies render nested under their root, not as separate entries.
    for (const c of comments) if (c.parent_id == null) for (const id of c.item_ids) {
      (m.get(id) ?? m.set(id, []).get(id)!).push(c);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [comments]);

  // Replies keyed by their thread root, for nesting in the report.
  const repliesByRoot = useMemo(() => {
    const m = new Map<number, Comment[]>();
    for (const c of comments) if (c.parent_id != null) (m.get(c.parent_id) ?? m.set(c.parent_id, []).get(c.parent_id)!).push(c);
    return m;
  }, [comments]);

  const open = comments.filter((c) => c.status !== "resolved").length;
  const resolved = comments.length - open;
  const generated = new Date().toLocaleString();

  // mailto: a compact text summary (capped — mailto bodies are length-limited; the PDF is the full record).
  const mailto = useMemo(() => {
    const lines = [
      `UGS Warehouse — review comments report (${generated})`,
      `${comments.length} comments · ${open} open · ${resolved} resolved · ${groups.length} items`,
      "",
      ...groups.slice(0, 25).map(([id, cs]) => {
        const o = cs.filter((c) => c.status !== "resolved").length;
        return `• ${id} — ${cs.length} comment${cs.length === 1 ? "" : "s"}${o ? ` (${o} open)` : ""}`;
      }),
      groups.length > 25 ? `…and ${groups.length - 25} more items` : "",
      "",
      "Full report exported as PDF from the review viewer.",
    ].filter((l) => l !== "");
    const body = lines.join("\n").slice(0, 1800);
    return `mailto:?subject=${encodeURIComponent(`UGS review comments — ${open} open`)}&body=${encodeURIComponent(body)}`;
  }, [groups, comments.length, open, resolved, generated]);

  return (
    <div className="review-report fixed inset-0 z-50 overflow-auto bg-background p-6">
      <div className="mx-auto max-w-3xl">
        {/* Controls — hidden in print output */}
        <div className="no-print mb-4 flex flex-wrap items-center gap-2">
          <button onClick={() => window.print()}
            className="rounded border border-border bg-primary px-3 py-1 text-sm text-primary-foreground hover:opacity-90">
            Export PDF (print)
          </button>
          <a href={mailto}
            className="rounded border border-border bg-card px-3 py-1 text-sm text-foreground no-underline hover:border-primary">
            Email draft
          </a>
          <button onClick={onClose}
            className="ml-auto rounded border border-border bg-card px-3 py-1 text-sm text-foreground hover:border-primary">
            Close
          </button>
        </div>

        <h1 className="text-xl font-bold">UGS Warehouse — Review Comments Report</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Generated {generated} · {comments.length} comment{comments.length === 1 ? "" : "s"} ·
          {" "}{open} open · {resolved} resolved · {groups.length} item{groups.length === 1 ? "" : "s"}
        </p>

        {isLoading && <p className="mt-4 text-sm text-muted-foreground">Loading…</p>}
        {error && <p className="mt-4 text-sm text-muted-foreground">Comments backend not available.</p>}
        {!isLoading && !error && comments.length === 0 && <p className="mt-4 text-sm text-muted-foreground">No comments.</p>}

        <div className="mt-4 space-y-5">
          {groups.map(([itemId, cs]) => (
            <section key={itemId} className="break-inside-avoid">
              <h2 className="border-b border-border pb-0.5 font-mono text-sm font-semibold">
                {/* clickable on screen, prints its text (buttons render their label in print) */}
                {onOpen
                  ? <button className="text-primary hover:underline print:text-black" onClick={() => onOpen(itemId)}>{itemId}</button>
                  : itemId}
                <span className="ml-2 font-sans text-xs font-normal text-muted-foreground">{cs.length} comment{cs.length === 1 ? "" : "s"}</span>
              </h2>
              <ul className="mt-1.5 space-y-1.5">
                {cs.map((c) => (
                  <li key={c.id} className="break-inside-avoid text-sm">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
                      <span className="rounded border border-border px-1 font-medium text-foreground">{targetLabel(c)}</span>
                      <span className="font-medium text-foreground">{c.author.split("@")[0]}</span>
                      <span>{new Date(c.created_at).toLocaleString()}</span>
                      <span className={c.status === "resolved" ? "text-green-600 dark:text-green-400" : "text-amber-600 dark:text-amber-400"}>
                        {c.status}
                      </span>
                    </div>
                    <p className="mt-0.5 whitespace-pre-wrap">{c.body}</p>
                    {(repliesByRoot.get(c.id) ?? []).map((r) => (
                      <div key={r.id} className="mt-1 border-l-2 border-border pl-2">
                        <span className="text-xs text-muted-foreground">{r.author.split("@")[0]} · {new Date(r.created_at).toLocaleString()}</span>
                        <p className="whitespace-pre-wrap">{r.body}</p>
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
