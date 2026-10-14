// The identify card for a click on a geologic-map raster: the unit under the point, or the scan-only
// notice. Styled like the feature card; announced politely and dismissed with Escape or the close button.
import { useEffect, useMemo, useState } from "react";
import { rootIndexItems, useRootIndex } from "@/stac";
import { type IdentifyResult, type PubContext, pubContext } from "./identify";

const CLAMP_AT = 160;

function Shell({ label, onClose, children }: { label: string; onClose?: () => void; children: React.ReactNode }) {
  useEffect(() => {
    if (!onClose) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <section role="status" aria-live="polite" aria-label={label} className="mt-3 rounded-md border border-border bg-card p-3">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</span>
        <button type="button" onClick={onClose} aria-label="Clear selection" className="text-muted-foreground hover:text-foreground">✕</button>
      </div>
      {children}
    </section>
  );
}

function Description({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > CLAMP_AT;
  return (
    <div className="mt-1.5 text-xs">
      <span style={open || !long ? undefined : { display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}
        className="block break-words">{text}</span>
      {long && (
        <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
          className="mt-0.5 font-medium text-primary hover:underline">{open ? "less" : "more"}</button>
      )}
    </div>
  );
}

// The card with its publication already resolved.
export function UnitCard({ result, pub, onClose }: { result: IdentifyResult; pub?: PubContext; onClose?: () => void }) {
  if (result.kind === "scan-only") {
    return (
      <Shell label="Geologic unit" onClose={onClose}>
        <div className="text-sm">No unit data for this map (scan only)</div>
        {result.pubTitle && <div className="mt-1 text-xs text-muted-foreground">{result.pubTitle}</div>}
      </Shell>
    );
  }
  const u = result.unit;
  const title = pub?.title ?? u.seriesId;
  return (
    <Shell label="Geologic unit" onClose={onClose}>
      <div className="text-sm"><span className="font-bold">{u.code}</span>{u.name && <> {u.name}</>}</div>
      {u.age && <div className="mt-1 text-xs"><span className="text-muted-foreground">Age</span> {u.age}</div>}
      {u.description && <Description text={u.description} />}
      {u.seriesId && (
        <div className="mt-2 border-t border-border pt-2 text-xs">
          From {pub?.href
            ? <a href={pub.href} className="font-medium text-primary hover:underline">{title}</a>
            : <span>{title}</span>}
          {pub?.dmuHref && (
            <a href={pub.dmuHref} className="mt-1 block font-medium text-primary hover:underline">View unit descriptions →</a>
          )}
        </div>
      )}
    </Shell>
  );
}

// Resolves the tile's series id against the root index the viewer already loads.
export function IdentifyCard({ result, onClose }: { result: IdentifyResult; onClose?: () => void }) {
  const root = useRootIndex(true);
  const entries = useMemo(() => (root.data ? rootIndexItems(root.data) : []), [root.data]);
  const pub = result.kind === "unit" && result.unit.seriesId ? pubContext(result.unit.seriesId, entries) : undefined;
  return <UnitCard result={result} pub={pub} onClose={onClose} />;
}
