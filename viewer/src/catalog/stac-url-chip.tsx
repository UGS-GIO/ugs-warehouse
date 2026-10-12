import { useRef, useState } from "react";
import { CheckIcon, CopyIcon } from "@/shell/icons";

// Absolute — a copied "/stac/…" from a relative ?catalog= override is useless elsewhere.
export async function copyCatalogUrl(url: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(new URL(url, location.href).href);
    return true;
  } catch {
    return false;   // denied, or a non-secure origin
  }
}

// The one string a QGIS/GeoLibre/pystac user needs, so copy is the primary action and the link is
// secondary. Hidden below md — the bar has no room beside the view tabs; NavMenu carries it there.
export function StacUrlChip({ url, className = "" }: { url: string; className?: string }) {
  const [state, setState] = useState<"idle" | "copied" | "manual">("idle");
  const link = useRef<HTMLAnchorElement>(null);
  const revert = useRef<ReturnType<typeof setTimeout>>(undefined);

  const copy = async () => {
    if (await copyCatalogUrl(url)) {
      setState("copied");
      // Restart, don't stack — a second click inside the window would otherwise revert early.
      clearTimeout(revert.current);
      revert.current = setTimeout(() => setState("idle"), 1500);
      return;
    }
    // No clipboard: select the URL so the keyboard still works.
    const node = link.current;
    if (node) {
      const range = document.createRange();
      range.selectNodeContents(node);
      const selection = getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    }
    setState("manual");
  };

  return (
    <span className={`hidden max-w-full items-center gap-1.5 text-xs text-muted-foreground md:inline-flex ${className}`}>
      <a ref={link} href={url} target="_blank" rel="noreferrer" title={url}
         className="truncate font-mono hover:text-foreground hover:underline">
        {url.replace(/^https?:\/\//, "")}
      </a>
      <button type="button" onClick={copy} aria-label="Copy the STAC catalog URL"
              title={state === "manual" ? "Press Ctrl/⌘C to copy" : "Copy the STAC catalog URL"}
              className="shrink-0 rounded p-0.5 hover:bg-accent hover:text-foreground">
        {state === "copied" ? <CheckIcon /> : <CopyIcon />}
      </button>
    </span>
  );
}
