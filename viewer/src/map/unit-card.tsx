// The identify card for a click on a geologic-map raster: the unit under the point, or the scan-only
// notice. Styled like the feature card; announced politely and dismissed with Escape or the close button.
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { rootIndexItems, useRootIndex } from "@/stac/items-index";
import { type IdentifyResult, type PubContext, pubContext } from "./identify";

const CLAMP_AT = 160;

const typing = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));

// Focus moves to the card's heading each time a new result opens, so a screen reader reads it.
function Shell({ label, result, onClose, children }: {
  label: string; result: IdentifyResult; onClose?: () => void; children: React.ReactNode;
}) {
  const head = useRef<HTMLSpanElement>(null);
  useEffect(() => { head.current?.focus({ preventScroll: true }); }, [result]);
  useEffect(() => {
    if (!onClose) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !typing(e.target)) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <section role="region" aria-label={label} className="mt-3 rounded-md border border-border bg-card p-3">
      <div className="mb-1.5 flex items-center justify-between">
        <span ref={head} tabIndex={-1} className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground outline-none">{label}</span>
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
      <Shell label="Geologic unit" result={result} onClose={onClose}>
        <div className="text-sm">No unit data for this map (scan only)</div>
        {result.pubTitle && <div className="mt-1 text-xs text-muted-foreground">{result.pubTitle}</div>}
      </Shell>
    );
  }
  const u = result.unit;
  const title = pub?.title ?? u.seriesId;
  return (
    <Shell label="Geologic unit" result={result} onClose={onClose}>
      <div className="text-sm"><span className="font-bold">{u.code}</span>{u.name && <> {u.name}</>}</div>
      {u.age && <div className="mt-1 text-xs"><span className="text-muted-foreground">Age</span> {u.age}</div>}
      {u.description && <Description text={u.description} />}
      {pub && !u.description && !pub.dmu && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          This unit's description is not recorded in the map's GIS data. It is in the publication
          {pub.pdf
            ? <>: <a href={pub.pdf.href} target="_blank" rel="noopener noreferrer"
                className="font-medium text-primary hover:underline">{pub.pdf.label} (PDF) ↗</a></>
            : "."}
        </p>
      )}
      {u.seriesId && (
        <div className="mt-2 border-t border-border pt-2 text-xs">
          From {pub?.page
            ? <Link to="/catalog" search={pub.page} className="font-medium text-primary hover:underline">{title}</Link>
            : <span>{title}</span>}
          {pub?.dmu && (
            <Link to="/catalog" search={pub.dmu} className="mt-1 block font-medium text-primary hover:underline">View unit descriptions →</Link>
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
