import { useState } from "react";
import { currentGeoparquetUrl, type DiffFeature, diffFeatures, diffLayers, type DiffSummary } from "./diff";

// "Compare to current" for a _review item — diffs its GeoParquet against the live _current version
// (matched by stem) client-side via duckdb-wasm, and shows what changed. Review deploy only.
type Kind = "added" | "removed" | "modified";
const DETAIL_LIMIT = 50;

export function DiffPanel({ stem, reviewParquetUrl }: { stem: string; reviewParquetUrl: string }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "nocurrent" | "error">("idle");
  const [sum, setSum] = useState<DiffSummary | null>(null);
  const [err, setErr] = useState("");
  // Drill-down: which category is expanded + its lazily-fetched features (cached per kind).
  const [open, setOpen] = useState<Kind | null>(null);
  const [detail, setDetail] = useState<Partial<Record<Kind, DiffFeature[]>>>({});
  const [detailBusy, setDetailBusy] = useState<Kind | null>(null);

  const currentUrl = currentGeoparquetUrl(stem);

  async function run() {
    setState("loading");
    setErr("");
    setOpen(null);
    setDetail({});
    try {
      const s = await diffLayers(reviewParquetUrl, currentUrl);
      setSum(s);
      setState("done");
    } catch (e) {
      const msg = String((e as Error)?.message ?? e);
      // A missing current baseline (404 on the range-read) → this is brand-new review data.
      if (/404|not[\s_-]?found|no files found|HTTP 4\d\d|failed to (open|read)/i.test(msg)) {
        setState("nocurrent");
      } else {
        setErr(msg);
        setState("error");
      }
    }
  }

  async function toggle(kind: Kind, count: number) {
    if (count === 0) return;
    if (open === kind) { setOpen(null); return; }
    setOpen(kind);
    if (detail[kind] || !sum) return;  // already fetched
    setDetailBusy(kind);
    try {
      const rows = await diffFeatures(reviewParquetUrl, currentUrl, kind, sum.sourceCols, DETAIL_LIMIT);
      setDetail((d) => ({ ...d, [kind]: rows }));
    } catch {
      setDetail((d) => ({ ...d, [kind]: [] }));
    } finally {
      setDetailBusy(null);
    }
  }

  const changed = sum ? sum.added + sum.removed + sum.modified : 0;
  const counts: Record<Kind, number> = { added: sum?.added ?? 0, removed: sum?.removed ?? 0, modified: sum?.modified ?? 0 };

  return (
    <div className="mt-2 rounded-md border border-border bg-card/50 p-2 text-xs">
      <div className="flex items-center gap-2">
        <button onClick={run} disabled={state === "loading"}
          className="rounded border border-border bg-background px-2 py-0.5 hover:bg-accent disabled:opacity-60">
          {state === "loading" ? "Comparing…" : "Compare to current"}
        </button>
        <span className="text-muted-foreground">what changed vs the live version</span>
      </div>

      {state === "nocurrent" && (
        <p className="mt-2 text-muted-foreground">No current version — this is new review data (nothing to compare).</p>
      )}
      {state === "error" && (
        <p className="mt-2 text-destructive">Diff failed: {err}</p>
      )}

      {state === "done" && sum && (
        <div className="mt-2 space-y-1.5">
          <div className="flex flex-wrap gap-1.5">
            <Badge active={open === "added"} disabled={!counts.added} onClick={() => toggle("added", counts.added)}
              className="border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400">+{sum.added} added</Badge>
            <Badge active={open === "removed"} disabled={!counts.removed} onClick={() => toggle("removed", counts.removed)}
              className="border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400">−{sum.removed} removed</Badge>
            <Badge active={open === "modified"} disabled={!counts.modified} onClick={() => toggle("modified", counts.modified)}
              className="border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400">~{sum.modified} modified</Badge>
            <Badge className="border-border bg-muted text-muted-foreground">{sum.unchanged} unchanged</Badge>
          </div>
          <p className="text-[11px] text-muted-foreground">
            {changed === 0 ? "No changes — identical to the current version. " : `${changed} feature${changed === 1 ? "" : "s"} changed — click a badge to inspect. `}
            review {sum.reviewRows.toLocaleString()} · current {sum.currentRows.toLocaleString()} rows ·
            {" "}{sum.sourceCols.length} attribute column{sum.sourceCols.length === 1 ? "" : "s"} compared
            {" "}(matched by geometry).
          </p>

          {open && (
            <Detail kind={open} busy={detailBusy === open} rows={detail[open]} total={counts[open]} cols={sum.sourceCols} />
          )}
        </div>
      )}
    </div>
  );
}

function Detail({ kind, busy, rows, total, cols }: {
  kind: Kind; busy: boolean; rows?: DiffFeature[]; total: number; cols: string[];
}) {
  if (busy) return <p className="text-muted-foreground">Loading {kind} features…</p>;
  if (!rows) return null;
  if (rows.length === 0) return <p className="text-muted-foreground">Couldn't load {kind} features.</p>;
  const capped = total > rows.length;

  return (
    <div className="rounded border border-border bg-background p-1.5">
      <div className="mb-1 text-[11px] text-muted-foreground">
        {kind === "modified" ? "Changed attributes (current → review)" : `${kind} features`}
        {capped ? ` · first ${rows.length} of ${total.toLocaleString()}` : ` · ${rows.length}`}
      </div>

      {kind === "modified" ? (
        <ul className="space-y-1.5">
          {rows.map((r, i) => (
            <li key={i} className="border-t border-border pt-1 first:border-0 first:pt-0">
              {(r.changes ?? []).length === 0
                ? <span className="text-muted-foreground">(fingerprint differs, no display-column change)</span>
                : (r.changes ?? []).map((c) => (
                    <div key={c.col} className="flex flex-wrap items-baseline gap-1">
                      <code className="text-foreground">{c.col}</code>
                      <span className="text-red-600 line-through dark:text-red-400">{fmt(c.from)}</span>
                      <span className="text-muted-foreground">→</span>
                      <span className="text-green-600 dark:text-green-400">{fmt(c.to)}</span>
                    </div>
                  ))}
            </li>
          ))}
        </ul>
      ) : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr>{cols.map((c) => <th key={c} className="border-b border-border px-1.5 py-0.5 text-left font-medium">{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => <td key={c} className="border-b border-border px-1.5 py-0.5" title={fmt(r.values[c])}>
                    <span className="block max-w-[200px] truncate">{fmt(r.values[c])}</span>
                  </td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const fmt = (v: unknown) => (v == null || v === "" ? "∅" : String(v));

function Badge({ children, className, onClick, active, disabled }: {
  children: React.ReactNode; className?: string;
  onClick?: () => void; active?: boolean; disabled?: boolean;
}) {
  const base = `rounded-full border px-2 py-0.5 text-[11px] ${className ?? ""}`;
  if (!onClick) return <span className={base}>{children}</span>;
  return (
    <button onClick={onClick} disabled={disabled}
      className={`${base} ${active ? "ring-1 ring-current" : ""} ${disabled ? "opacity-50" : "cursor-pointer hover:brightness-110"}`}>
      {children}
    </button>
  );
}
