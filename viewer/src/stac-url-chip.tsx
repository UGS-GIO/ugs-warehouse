import { useRef, useState } from "react";

// The catalog's address, shown where people land rather than buried in the footer: it is the one
// string a QGIS / GeoLibre / pystac user needs, and they need to COPY it, not click it. So the URL
// is spelled out and the primary action is copy; the link is the secondary affordance.
export function StacUrlChip({ url, className = "" }: { url: string; className?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "manual">("idle");
  const link = useRef<HTMLAnchorElement>(null);

  const copy = async () => {
    // Absolute, even when the viewer runs off a relative ?catalog= override — a copied "/stac/…"
    // is useless in another app.
    try {
      await navigator.clipboard.writeText(new URL(url, location.href).href);
      setState("copied");
      setTimeout(() => setState("idle"), 1500);
    } catch {
      // Clipboard unavailable — denied, or a non-secure origin. Select the URL so the keyboard
      // still works rather than leaving a button that visibly does nothing.
      const node = link.current;
      if (node) {
        const range = document.createRange();
        range.selectNodeContents(node);
        const selection = getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
      setState("manual");
    }
  };

  return (
    <div className={`inline-flex max-w-full flex-wrap items-center gap-2 rounded-md border border-white/25 bg-black/30 px-3 py-1.5 text-xs backdrop-blur-sm ${className}`}>
      <span className="font-semibold uppercase tracking-wider text-white/80">STAC catalog</span>
      <a ref={link} href={url} target="_blank" rel="noreferrer" title={url}
         className="truncate font-mono text-white/95 underline decoration-white/40 underline-offset-2 hover:decoration-white">
        {url.replace(/^https?:\/\//, "")}
      </a>
      <button type="button" onClick={copy}
              className="rounded border border-white/30 px-2 py-0.5 font-medium text-white/90 hover:bg-white/15">
        {state === "copied" ? "Copied" : state === "manual" ? "Selected — press Ctrl/⌘C" : "Copy"}
      </button>
      <span className="text-white/70">Paste into QGIS, GeoLibre, or pystac</span>
    </div>
  );
}
