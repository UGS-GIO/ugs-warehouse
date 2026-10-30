// "Developer" section of the item-detail page: view the exact STAC item.json the CDN served (the
// ground truth behind the rendered panels), copy it, or open the raw .json. Base UI's Dialog (as
// the What's-here picker uses) handles focus, Escape, the scroll lock and the portal.
import { useMemo } from "react";
import { Dialog } from "@base-ui/react/dialog";

import { CopyBtn } from "./endpoints-panel";
import { selfHrefOf, type StacDoc } from "@/stac";

const closeX = "flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-hover hover:text-foreground pointer-coarse:min-h-11 pointer-coarse:min-w-11";

export function StacJson({ item, base, title = "STAC item JSON" }: { item: StacDoc; base?: string; title?: string }) {
  const json = useMemo(() => JSON.stringify(item, null, 2), [item]);
  // The published self link is relative (./{id}.json): resolve it against where the doc came from,
  // not the viewer page. Without a base, a relative link would 404, so offer none.
  const self = base ? selfHrefOf(item, base) : undefined;
  const openLink = self && (
    <a href={self} target="_blank" rel="noopener" className="text-sm text-primary no-underline hover:underline">Open .json ↗</a>
  );

  return (
    <Dialog.Root>
      <div className="flex flex-wrap items-center gap-2">
        <Dialog.Trigger
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:border-primary hover:text-primary">
          View STAC JSON
        </Dialog.Trigger>
        {openLink}
      </div>
      <Dialog.Portal>
        {/* Above the Utah header, which sits at z-index 3000 and otherwise covers the title bar. */}
        <Dialog.Backdrop className="fixed inset-0 z-3100 bg-black/50" />
        <Dialog.Popup className="fixed left-1/2 top-1/2 z-3101 flex max-h-modal w-[calc(100vw-2rem)] max-w-5xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-2xl">
          <div className="flex items-center gap-3 border-b border-border px-4 py-3">
            <Dialog.Title className="min-w-0 flex-1 truncate text-base font-semibold text-card-foreground">{title}</Dialog.Title>
            {openLink}
            <CopyBtn text={json} className="px-2.5 py-1 text-sm" />
            <Dialog.Close className={closeX} aria-label="Close">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" aria-hidden><path d="M6 6l12 12M18 6L6 18" /></svg>
            </Dialog.Close>
          </div>
          <pre className="min-h-0 flex-1 overflow-auto bg-muted/40 p-4 font-mono text-xs leading-relaxed text-card-foreground">{json}</pre>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
