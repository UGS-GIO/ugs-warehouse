import { useIsFetching } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { createContext, Suspense, useContext } from "react";
import utahLogo from "./assets/utah-logo.png";
import { MapSearch } from "./shell/map-search";
import { LegalFooter } from "./shell/legal-footer";
import { SavingNotice } from "./offline/saving-notice";
import { OfflineBadge } from "./offline/offline-notice";
import { NavMenu } from "./shell/nav-menu";
import type { View } from "./shell/nav";
import { useViewState } from "./shell/view-state";
import { PreviewMapProvider } from "./map/preview-map";
import { CATALOG_URL, IS_REVIEW } from "./stac";
import { StacUrlChip } from "./catalog/stac-url-chip";
import { NotifBell } from "./review/notifications-inbox";
import { DataSaverBadge } from "./shell/data-saver-badge";


// collKeyOf + idOf live in the data layer (stac.ts) next to CATALOG_URL. idOf is re-exported here so
// the existing `@/app` importers keep resolving it from this module.
export { idOf } from "./stac";

// Primary tabs: the desktop tab row + the mobile menu's "Views". Discover leads (it's the star).
const PRIMARY_VIEWS: { id: View; label: string }[] = [
  { id: "discover", label: "Discover" },
  { id: "map", label: "Map" },
  { id: "catalog", label: "Catalog" },
];
// Secondary views — always in the NavMenu overflow (desktop + mobile) so the tab row never overflows.
const OVERFLOW_VIEWS: { id: View; label: string }[] = [
  { id: "guide", label: "Guide" },
  { id: "offline", label: "Offline data" },
  { id: "settings", label: "Settings" },
  ...(IS_REVIEW ? [{ id: "review" as const, label: "Review" }] : []),
];

const tab = (on: boolean) =>
  "cursor-pointer border-b-2 px-2 py-1 text-sm transition-colors "
  + (on ? "border-primary font-medium text-primary" : "border-transparent text-muted-foreground hover:text-foreground");

// Thin top progress bar — visible while any TanStack Query fetch is in flight OR a view switch is
// pending (useTransition). A global "working" signal so a slow load never reads as a frozen app.
const SEARCH_BOX_QUERIES = new Set(["place-suggest", "header-search-index", "quad-gazetteer", "utm", "plss", "unit-index"]);

function FetchBar({ pending }: { pending?: boolean }) {
  // The search box has its own spinner.
  const busy = useIsFetching({ predicate: (q) => !SEARCH_BOX_QUERIES.has(String(q.queryKey[0])) }) > 0 || pending;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-0 z-50 h-0.5 overflow-hidden">
      {busy && <div className="fetch-bar h-full w-full bg-primary" />}
    </div>
  );
}

/** Everything the view routes need. Inferred from useViewState so the two cannot drift. */
export type ViewCtx = ReturnType<typeof useViewState>;
const ViewContext = createContext<ViewCtx | null>(null);

export const useViewCtx = (): ViewCtx => {
  const ctx = useContext(ViewContext);
  if (!ctx) throw new Error("useViewCtx must be used inside AppLayout");
  return ctx;
};

/** The map search, wired to the view state. */
export function MapSearchFor({ state, className }: { state: ViewCtx; className?: string }) {
  return <MapSearch items={state.mapItems} loadKey={state.mapLoadKey} isLayer={state.searchIsLayer}
    onPlace={state.pickPlace} onItem={state.pickSearchItem}
    onSearchAll={(q) => state.openDiscoverSearch({ q })} className={className} />;
}

/** Root layout route: owns the data + shell, renders the matched view through <Outlet />. */
export function AppLayout() {
  const state = useViewState();
  const { view, setView, catalog, lockedView, pending } = state;
  return (
    // One persistent preview map lives in this provider (mounted once, above the view/list/item
    // boundary) so item navigation swaps sources instead of churning WebGL contexts. See PreviewMap.
    <PreviewMapProvider>
    <div className={lockedView
      ? "grid h-full grid-cols-1 grid-rows-[auto_auto_1fr_auto] overflow-hidden bg-background text-sm text-foreground"
      : "flex h-full flex-col overflow-y-auto overflow-x-hidden bg-background text-sm text-foreground"}>
      <FetchBar pending={pending} />
      <header className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-background px-3 py-2 sm:px-4 ${lockedView ? "" : "sticky top-0 z-20"}`}>
        <Link to="/" title="Home"
          className="flex items-center gap-2 whitespace-nowrap hover:opacity-80">
          {/* The emblem shows only where index.css hides the state band (phones, short screens). Where
              the band shows it already carries the beehive, and a second copy read as a duplicate. */}
          <span className="app-emblem shrink-0 items-center gap-2">
            <img src={utahLogo} alt="Utah Geological Survey" className="h-5 w-auto dark:brightness-0 dark:invert" />
            <span aria-hidden className="hidden text-sm font-semibold text-muted-foreground md:inline">Utah Geological Survey</span>
            <span aria-hidden className="mr-1 hidden h-4 w-px bg-border md:inline" />
          </span>
          <strong className="font-display text-xl tracking-tight">UGS Warehouse</strong>
        </Link>
        {/* Beside the name, not in a hero — the URL applies to every view. */}
        <StacUrlChip url={CATALOG_URL} />
        <div className="ml-auto flex items-center gap-1">
          {/* The same views twice, but only one is ever rendered: tabs where they fit, hamburger
              below md — five tabs and a phone don't share a row. */}
          <div className="hidden gap-1 md:flex">
            {PRIMARY_VIEWS.map((v) => (
              <button key={v.id} type="button" aria-current={view === v.id ? "page" : undefined}
                className={tab(view === v.id)} onClick={() => setView(v.id)}>{v.label}</button>
            ))}
          </div>
          <DataSaverBadge />
          <OfflineBadge />
          {IS_REVIEW && <NotifBell onClick={() => setView("review")} />}
          {/* Always mounted: it carries the theme picker + the overflow views, and below md the
              primary tabs as well. */}
          <NavMenu current={view} catalogUrl={CATALOG_URL}
            pages={PRIMARY_VIEWS.map((v) => ({ id: v.id, label: v.label, onSelect: () => setView(v.id) }))}
            overflow={OVERFLOW_VIEWS.map((v) => ({ id: v.id, label: v.label, onSelect: () => setView(v.id) }))} />
        </div>
      </header>

      {/* Always mounted, empty when there is no error, so the locked grid keeps its rows. */}
      <div>{catalog.error && <p className="p-4 text-destructive">{String(catalog.error)}</p>}</div>

      <Suspense fallback={<div className="flex items-center justify-center p-16 text-sm text-muted-foreground">Loading…</div>}>
      <ViewContext.Provider value={state}>
        <Outlet />
        <SavingNotice />
      </ViewContext.Provider>
      </Suspense>
      {/* Every view, full-height ones included: it carries the state's required links. */}
      <LegalFooter className="mt-auto" catalogUrl={CATALOG_URL} />
    </div>
    </PreviewMapProvider>
  );
}
