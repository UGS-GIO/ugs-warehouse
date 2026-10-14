import { Popover } from "@base-ui/react/popover";
import { useState } from "react";

import { bibtex, type CitationFields, plainCitation, ris } from "./citation";

type Status = { key: string; state: "copied" | "error"; message?: string };

const ROW = "w-full rounded px-2 py-1.5 text-left text-sm hover:bg-muted";

// Sits beside the DOI link: copy the citation or BibTeX, or download RIS.
export function CiteMenu({ fields, className }: { fields: CitationFields; className: string }) {
  const [status, setStatus] = useState<Status | undefined>();

  const copy = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setStatus({ key, state: "copied" });
    } catch (e) {
      setStatus({ key, state: "error", message: e instanceof Error ? e.message : String(e) });
    }
  };
  const downloadRis = () => {
    const url = URL.createObjectURL(new Blob([ris(fields)], { type: "application/x-research-info-systems" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${fields.sid}.ris`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const label = (key: string, text: string) =>
    status?.key === key ? (status.state === "copied" ? "Copied" : "Copy failed") : text;

  return (
    <Popover.Root onOpenChange={() => setStatus(undefined)}>
      <Popover.Trigger className={className}>Cite as ▾</Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Popover.Popup className="w-56 space-y-1 rounded-md border border-border bg-card p-2 text-sm text-foreground shadow-lg outline-none">
            <Popover.Title className="px-2 text-xs font-semibold text-muted-foreground">Cite</Popover.Title>
            <button type="button" className={ROW} onClick={() => copy("text", plainCitation(fields))}>
              {label("text", "Copy citation")}
            </button>
            <button type="button" className={ROW} onClick={() => copy("bib", bibtex(fields))}>
              {label("bib", "Copy BibTeX")}
            </button>
            <button type="button" className={ROW} onClick={downloadRis}>Download RIS</button>
            {status?.state === "error" && (
              <p role="alert" className="px-2 text-xs text-destructive">
                Could not copy: {status.message || "clipboard unavailable"}
              </p>
            )}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
