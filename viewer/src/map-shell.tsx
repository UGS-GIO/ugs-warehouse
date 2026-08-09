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
import type { ReactNode, RefObject } from "react";
import { useRef, useState, useSyncExternalStore } from "react";

import { LegalFooter } from "./legal-footer";
import { CATALOG_URL } from "./stac";
import { clampSize, DETENTS, nearestDetent } from "./map-model";

type Tab = "layers" | "info";
type RevealRef = RefObject<(() => void) | null>;

const SIDEBAR_KEY = "ugsw.mapSidebarW";
const DOCK_KEY = "ugsw.mapDockH";
const SIDEBAR = { initial: 320, min: 240, max: 560 };
const DOCK = { initial: 240, min: 120, max: 640 };

// md: — the breakpoint the rest of the viewer already switches on. A media query IS an external
// store, so subscribe to it directly: no effect, and no first paint at the wrong breakpoint.
const MD = window.matchMedia("(min-width: 768px)");
const subscribeMd = (onChange: () => void) => {
  MD.addEventListener("change", onChange);
  return () => MD.removeEventListener("change", onChange);
};
const useIsDesktop = () => useSyncExternalStore(subscribeMd, () => MD.matches);

/** A drag-resizable size persisted to localStorage. "x" grows rightward, "y" grows UPWARD. */
function useResizable(key: string, { initial, min, max }: typeof SIDEBAR, axis: "x" | "y") {
  const [size, setSize] = useState(() => clampSize(Number(localStorage.getItem(key)), min, max, initial));
  const commit = (n: number) => {
    const v = clampSize(n, min, max, initial);
    setSize(v);
    localStorage.setItem(key, String(v));
  };
  const onPointerDown = (e: React.PointerEvent) => {
    e.preventDefault();
    const start = axis === "x" ? e.clientX : e.clientY;
    const startSize = size;
    const delta = (ev: PointerEvent) => (axis === "x" ? ev.clientX - start : start - ev.clientY);
    const move = (ev: PointerEvent) => setSize(clampSize(startSize + delta(ev), min, max, initial));
    const up = (ev: PointerEvent) => {
      commit(startSize + delta(ev));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.style.cursor = "";
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    document.body.style.cursor = axis === "x" ? "col-resize" : "row-resize";
  };
  return { size, onPointerDown, nudge: (d: number) => commit(size + d) };
}

const grip = "bg-muted-foreground/30 transition-colors group-hover:bg-primary group-focus:bg-primary";

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
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize layer list"
        tabIndex={0}
        onPointerDown={sidebar.onPointerDown}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") sidebar.nudge(-16);
          else if (e.key === "ArrowRight") sidebar.nudge(16);
        }}
        className="group relative z-10 -mx-1.5 flex w-4 shrink-0 cursor-col-resize items-center justify-center focus:outline-none"
      >
        <span className={`absolute h-10 w-1.5 rounded-full ${grip}`} />
      </div>
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">{map}</div>
        <section style={dockOpen ? { height: dock.size } : undefined} className="relative flex shrink-0 flex-col border-t border-border">
          {dockOpen && (
            <div
              role="separator"
              aria-orientation="horizontal"
              aria-label="Resize detail panel"
              tabIndex={0}
              onPointerDown={dock.onPointerDown}
              onKeyDown={(e) => {
                if (e.key === "ArrowUp") dock.nudge(16);
                else if (e.key === "ArrowDown") dock.nudge(-16);
              }}
              className="group absolute inset-x-0 -top-1.5 z-10 flex h-3 cursor-row-resize items-center justify-center focus:outline-none"
            >
              <span className={`h-1.5 w-10 rounded-full ${grip}`} />
            </div>
          )}
          <button
            type="button"
            onClick={() => setDockOpen((o) => !o)}
            aria-expanded={dockOpen}
            className="flex items-center gap-2 px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground"
          >
            <span>{dockOpen ? "▾" : "▸"}</span> Item detail
          </button>
          {dockOpen && <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">{info}</div>}
          <LegalFooter catalogUrl={CATALOG_URL} />
        </section>
      </main>
    </div>
  );
}

const icon = "h-5 w-5";
// No "Map" tab: the map is never hidden, it's what the sheet sits on. Tapping the open tab drops
// the sheet back to a peek, which is the move a Map button was standing in for.
const TABS: { id: Tab; label: string; path: ReactNode }[] = [
  { id: "layers", label: "Layers", path: <><path d="m12 2 9 5-9 5-9-5 9-5Z" /><path d="m3 12 9 5 9-5" /><path d="m3 17 9 5 9-5" /></> },
  { id: "info", label: "Info", path: <><circle cx="12" cy="12" r="9" /><path d="M12 16v-5M12 8h.01" /></> },
];

/** Mobile: full map + a draggable sheet anchored above a persistent tab bar, which picks what the
 * sheet holds. Detent 0 (a peek) is the map view; `tab` is only what's shown when it's raised. */
function MobileShell({ map, layers, info, revealInfo }: ShellProps) {
  const [tab, setTab] = useState<Tab>("layers");
  const [detent, setDetent] = useState(0);
  const [dragH, setDragH] = useState<number | null>(null);
  const areaRef = useRef<HTMLDivElement>(null);
  const collapsed = detent === 0;

  revealInfo.current = () => {
    setTab("info");
    setDetent((d) => Math.max(d, 1));
  };

  const selectTab = (t: Tab) => {
    if (t === tab && !collapsed) return setDetent(0);   // tap the open tab to get the map back
    setTab(t);
    setDetent((d) => Math.max(d, 1));
  };

  const startDrag = (e: React.PointerEvent) => {
    const startY = e.clientY;
    const height = areaRef.current?.clientHeight ?? window.innerHeight;
    const startH = height * DETENTS[detent];
    const clamp = (h: number) => clampSize(h, height * 0.06, height * 0.94, startH);
    const move = (ev: PointerEvent) => setDragH(clamp(startH + (startY - ev.clientY)));
    const up = (ev: PointerEvent) => {
      setDetent(nearestDetent(clamp(startH + (startY - ev.clientY)) / height));
      setDragH(null);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={areaRef} className="relative min-h-0 flex-1">
        <div className="absolute inset-0">{map}</div>
        <div
          style={dragH != null ? { height: `${dragH}px` } : { height: `${DETENTS[detent] * 100}%` }}
          className={"absolute inset-x-0 bottom-0 z-20 flex flex-col rounded-t-2xl border-t border-border bg-background shadow-2xl "
            + (dragH == null ? "transition-[height] duration-300 ease-out" : "")}
        >
          <div onPointerDown={startDrag} className="flex shrink-0 cursor-grab touch-none items-center justify-center py-2.5">
            <span className="h-1.5 w-10 rounded-full bg-border" />
          </div>
          {!collapsed && (
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="px-3 pb-3">{tab === "layers" ? layers : info}</div>
              <LegalFooter catalogUrl={CATALOG_URL} />
            </div>
          )}
        </div>
      </div>
      <nav aria-label="Map views" className="z-30 flex shrink-0 border-t border-border" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => selectTab(t.id)}
            aria-expanded={tab === t.id && !collapsed}
            className={"flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] font-medium "
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
