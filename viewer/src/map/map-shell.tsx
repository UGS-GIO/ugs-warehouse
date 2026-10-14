/**
 * Layout for the map view. Desktop: resizable layer sidebar · map · collapsible detail dock, all
 * visible together. Mobile: full-bleed map under one draggable sheet (peek/half/full) whose tab bar
 * picks what's in it — the standard mobile-map pattern, one thumb-reachable surface.
 *
 * The map is passed in and rendered ONCE in both branches, so switching tabs slides a sheet over a
 * live map instead of tearing down its WebGL context.
 *
 * Adapted from the same shell in ugs-soil-water-model's viewer; the tabs differ because our third
 * surface is an item's metadata, not a chart.
 */
import { Drawer } from "@base-ui/react/drawer";
import { useNavigate, useSearch } from "@tanstack/react-router";
import type { ReactNode, RefObject } from "react";
import { useRef, useState } from "react";

import { DETENTS, parseSheet, sheetParam, type SheetState, type SheetTab } from "./map-model";
import { useIsDesktop } from "@/ui/use-breakpoint";
import { ResizeHandle } from "@/ui/resizable";
import { useResizable } from "@/ui/use-resizable";

type RevealRef = RefObject<(() => void) | null>;

const SIDEBAR_KEY = "ugsw.mapSidebarW";
const DOCK_KEY = "ugsw.mapDockH";
const SIDEBAR = { initial: 320, min: 240, max: 560 };
const DOCK = { initial: 240, min: 120, max: 640 };

function DesktopShell({ map, layers, info, revealInfo }: ShellProps) {
  const [dockOpen, setDockOpen] = useState(true);
  const sidebar = useResizable(SIDEBAR_KEY, SIDEBAR, "x");
  const dock = useResizable(DOCK_KEY, DOCK, "y");
  revealInfo.current = () => setDockOpen(true);

  return (
    <div className="flex h-full min-h-0">
      <aside style={{ width: sidebar.size }} className="flex shrink-0 flex-col overflow-y-auto border-r border-border p-3">
        {layers}
      </aside>
      <ResizeHandle resizable={sidebar} label="Resize layer list" className="relative z-10 -mx-1.5 shrink-0" />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">{map}</div>
        <section style={dockOpen ? { height: dock.size } : undefined} className="relative flex shrink-0 flex-col border-t border-border">
          {dockOpen && (
            <ResizeHandle resizable={dock} label="Resize detail panel" className="absolute inset-x-0 -top-1.5 z-10" />
          )}
          <button
            type="button"
            onClick={() => setDockOpen((o) => !o)}
            aria-expanded={dockOpen}
            className="flex items-center gap-2 px-3 py-1.5 text-sm font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground"
          >
            <span>{dockOpen ? "▾" : "▸"}</span> Item detail
          </button>
          {dockOpen && <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">{info}</div>}
        </section>
      </main>
    </div>
  );
}

const icon = "h-5 w-5";
// No text selection, tap highlight or callout menu on a long press of the sheet's chrome.
const GRAB = "select-none [-webkit-touch-callout:none] [-webkit-tap-highlight-color:transparent]";
const SNAP_POINTS: number[] = [...DETENTS];
const SHEET_LABEL = ["Expand panel", "Expand panel to full height", "Collapse panel"];
const noMenu = (e: React.MouseEvent) => e.preventDefault();
// No "Map" tab: the map is never hidden, it's what the sheet sits on. Tapping the open tab drops
// the sheet back to a peek, which is the move a Map button was standing in for.
const TABS: { id: SheetTab; label: string; path: ReactNode }[] = [
  { id: "layers", label: "Layers", path: <><path d="m12 2 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 17 9 5 9-5" /></> },
  { id: "info", label: "Info", path: <><circle cx="12" cy="12" r="9" /><path d="M12 16v-5M12 8h.01" /></> },
];

/** Mobile: full map + a draggable sheet anchored above a persistent tab bar, which picks what the
 * sheet holds. Detent 0 (a peek) is the map view; `tab` is only what's shown when it's raised.
 * Both live in the URL: a reload or a shared link keeps the sheet, and Back undoes a tap. */
function MobileShell({ map, layers, info, revealInfo }: ShellProps) {
  const { tab, detent } = parseSheet(useSearch({ from: "__root__", select: (s) => s.sheet }));
  const navigate = useNavigate();
  // Taps push a history entry; drags and reveals replace, so Back doesn't replay every drag.
  const setSheet = (next: SheetState, push = false) =>
    navigate({ to: ".", replace: !push, search: (prev) => ({ ...prev, sheet: sheetParam(next) }) });
  const setDetent = (d: number) => setSheet({ tab, detent: d });
  const areaRef = useRef<HTMLDivElement>(null);
  const swipedOff = useRef(false);
  const collapsed = detent === 0;

  revealInfo.current = () => setSheet({ tab: "info", detent: Math.max(detent, 1) });

  const selectTab = (t: SheetTab) => {
    // Tap the open tab to get the map back.
    setSheet(t === tab && !collapsed ? { tab, detent: 0 } : { tab: t, detent: Math.max(detent, 1) }, true);
  };

  // Tap the grabber: peek → half → full → peek.
  const stepSheet = () => setSheet({ tab, detent: (detent + 1) % DETENTS.length }, true);
  const arrowSheet = (e: React.KeyboardEvent) => {
    const step = e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    setDetent(Math.min(Math.max(detent + step, 0), DETENTS.length - 1));
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={areaRef} className="relative min-h-0 flex-1">
        <div style={{ "--peek": `${DETENTS[0] * 100}%` }}
          className="phone-map absolute inset-0">{map}</div>
      </div>
      {/* Base UI's Drawer does the drag, flick and scroll handoff. It never closes: a swipe past the
          peek or Escape lands on the peek. Rendered after the area so the portal finds its ref. */}
      <Drawer.Root
        open
        modal={false}
        disablePointerDismissal
        snapPoints={SNAP_POINTS}
        snapPoint={DETENTS[detent]}
        snapToSequentialPoints
        onSnapPointChange={(p, details) => {
          if (p === null) details.cancel();
          // A cancelled swipe-dismiss is followed by Base UI restoring the old snap point.
          const restoring = swipedOff.current;
          swipedOff.current = false;
          setDetent(restoring ? 0 : Math.max(DETENTS.findIndex((d) => d === p), 0));
        }}
        onOpenChange={(open, details) => {
          if (open) return;
          details.cancel();
          swipedOff.current = details.reason === "swipe";
          setDetent(0);
        }}
      >
        <Drawer.Portal container={areaRef}>
          <Drawer.Viewport className="pointer-events-none absolute inset-0 z-20 flex items-end">
            <Drawer.Popup
              initialFocus={false}
              finalFocus={false}
              style={{ height: `${DETENTS[DETENTS.length - 1] * 100}%` }}
              className="pointer-events-auto relative flex w-full flex-col rounded-t-2xl border-t border-border bg-background shadow-2xl outline-none [padding-bottom:max(0px,calc(var(--drawer-snap-point-offset)+var(--drawer-swipe-movement-y)))] [transform:translateY(calc(var(--drawer-snap-point-offset)+var(--drawer-swipe-movement-y)))] transition-transform duration-300 ease-out data-swiping:duration-0 motion-reduce:transition-none">
              <Drawer.Title className="sr-only">{tab === "layers" ? "Layers" : "Item detail"}</Drawer.Title>
              {/* A button too, so each detent is a tap or an arrow key away, not only a drag (WCAG 2.5.7). */}
              <button type="button" onContextMenu={noMenu} onClick={stepSheet} onKeyDown={arrowSheet}
                aria-label={SHEET_LABEL[detent]}
                className={`flex shrink-0 cursor-grab touch-none items-center justify-center py-3 ${GRAB}`}>
                <span className="h-1.5 w-10 rounded-full bg-border" />
              </button>
              {!collapsed && (
                <Drawer.Content className="min-h-0 flex-1 touch-auto overflow-y-auto overscroll-contain">
                  <div className="px-3 pb-3">
                    {/* The detail's way back: an installed app has no browser Back button. */}
                    {tab === "info" && (
                      <button type="button" onClick={() => setSheet({ tab: "layers", detent }, true)}
                        className="-ml-1 mb-1 inline-flex items-center gap-1 rounded px-1 text-sm text-primary hover:underline">
                        <span aria-hidden>‹</span> Layers
                      </button>
                    )}
                    {tab === "layers" ? layers : info}
                  </div>
                </Drawer.Content>
              )}
            </Drawer.Popup>
          </Drawer.Viewport>
        </Drawer.Portal>
      </Drawer.Root>
      <nav aria-label="Map views" onContextMenu={noMenu} className={`z-30 flex shrink-0 border-t border-border ${GRAB}`} style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => selectTab(t.id)}
            aria-expanded={tab === t.id && !collapsed}
            className={"flex flex-1 flex-col items-center gap-0.5 py-2 text-sm font-medium "
              + (tab === t.id && !collapsed ? "text-primary" : "text-muted-foreground hover:text-foreground")}
          >
            <svg className={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              {t.path}
            </svg>
            <span>{t.label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}

type ShellProps = { map: ReactNode; layers: ReactNode; info: ReactNode; revealInfo: RevealRef };

export function MapShell(props: ShellProps) {
  return useIsDesktop() ? <DesktopShell {...props} /> : <MobileShell {...props} />;
}
