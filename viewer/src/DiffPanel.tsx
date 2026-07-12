import { useState } from "react";
import { currentGeoparquetUrl, diffLayers, type DiffSummary } from "./diff";

// "Compare to current" for a _review item — diffs its GeoParquet against the live _current version
// (matched by stem) client-side via duckdb-wasm, and shows what changed. Review deploy only.
export function DiffPanel({ stem, reviewParquetUrl }: { stem: string; reviewParquetUrl: string }) {
  const [state, setState] = useState<"idle" | "loading" | "done" | "nocurrent" | "error">("idle");
  const [sum, setSum] = useState<DiffSummary | null>(null);
  const [err, setErr] = useState("");

  async function run() {
    setState("loading");
    setErr("");
    try {
      const s = await diffLayers(reviewParquetUrl, currentGeoparquetUrl(stem));
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

  const changed = sum ? sum.added + sum.removed + sum.modified : 0;

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
            <Badge className="border-green-500/40 bg-green-500/10 text-green-600 dark:text-green-400">+{sum.added} added</Badge>
            <Badge className="border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400">−{sum.removed} removed</Badge>
            <Badge className="border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400">~{sum.modified} modified</Badge>
            <Badge className="border-border bg-muted text-muted-foreground">{sum.unchanged} unchanged</Badge>
          </div>
          <p className="text-[11px] text-muted-foreground">
            {changed === 0 ? "No changes — identical to the current version. " : `${changed} feature${changed === 1 ? "" : "s"} changed. `}
            review {sum.reviewRows.toLocaleString()} · current {sum.currentRows.toLocaleString()} rows ·
            {" "}{sum.sourceCols.length} attribute column{sum.sourceCols.length === 1 ? "" : "s"} compared
            {" "}(matched by geometry).
          </p>
        </div>
      )}
    </div>
  );
}

function Badge({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={`rounded-full border px-2 py-0.5 text-[11px] ${className ?? ""}`}>{children}</span>;
}
