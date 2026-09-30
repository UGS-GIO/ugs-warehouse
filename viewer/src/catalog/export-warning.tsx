// The pre-flight's findings, shown in place of the download until the user picks a way on.
import { useCallback } from "react";

import { holdsOneGeomType } from "@/data/download";

import { findingsHeading, type Warning } from "./export-findings";

export function ExportWarning({ warn, onUseGeoPackage, onForce, onDismiss }: {
  warn: Warning; onUseGeoPackage: () => void; onForce: () => void; onDismiss: () => void;
}) {
  // Stable identity: an inline arrow is a new ref every commit, so React would re-run it on each
  // render and steal focus back from the clip and CRS inputs the warning tells the user to use.
  const focusBox = useCallback((el: HTMLDivElement | null) => { el?.focus(); }, []);
  const tooBig = warn.findings.some((f) => f.level === "too-big");
  return (
    <div aria-labelledby="dl-warn-title" aria-describedby="dl-warn-why" tabIndex={-1} ref={focusBox}
      onKeyDown={(e) => { if (e.key === "Escape") onDismiss(); }}
      className="mt-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-sm">
      <div id="dl-warn-title" className="font-semibold text-amber-700 dark:text-amber-400">
        {findingsHeading(warn.findings, warn.fmt)}
      </div>
      <ul id="dl-warn-why" className="mt-1 list-disc space-y-0.5 pl-4 text-foreground">
        {warn.findings.map((f) => <li key={f.id}><b>{f.title}</b> {f.detail}</li>)}
      </ul>
      <div className="mt-2 flex flex-wrap gap-2">
        {/* GeoPackage shares the tab and the wasm instance, so it is no way out of a memory
            ceiling — only out of the limits the single-geometry formats impose. */}
        {!tooBig && holdsOneGeomType(warn.fmt) && (
          <button onClick={onUseGeoPackage}
            className="rounded border border-border bg-primary px-2 py-0.5 text-primary-foreground hover:opacity-90">
            Use GeoPackage instead
          </button>
        )}
        {!warn.findings.some((f) => f.noForce) && (
          <button onClick={onForce}
            className="rounded border border-border bg-card px-2 py-0.5 text-foreground hover:border-primary">
            Download anyway
          </button>
        )}
        <button onClick={onDismiss} className="text-muted-foreground hover:underline">Cancel</button>
      </div>
    </div>
  );
}
