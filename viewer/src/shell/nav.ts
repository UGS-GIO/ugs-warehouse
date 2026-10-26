import { useNavigate, useRouterState, useSearch } from "@tanstack/react-router";
import { layerParam, parseLayerParam } from "@/map/map-model";
import { setPin } from "@/map/camera";
import { DISCOVER_KEYS } from "@/discover/discovery-model";

// `s` = selected data-series codes (DS, OFR, GQ…) — shareable series filter for a collection.
export type View = "catalog" | "map" | "discover" | "guide" | "developers" | "preview" | "review" | "offline" | "settings";
// `satisfies` keeps each value a literal, so `navigate({ to })` typechecks against the generated
// route tree — a computed `/${view}` string would not, which is what the old cast papered over.
const VIEW_PATH = {
  catalog: "/catalog", map: "/map", discover: "/",
  guide: "/guide", developers: "/developers", preview: "/preview",
  review: "/review", offline: "/offline", settings: "/settings",
} satisfies Record<View, string>;
const isView = (v: string): v is View => v in VIEW_PATH;
const withoutDiscover = (search: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(search).filter(([k]) => !DISCOVER_KEYS.includes(k)));
export type Nav = { view: View; c?: string; i?: string; l?: string[]; s?: string[]; sheet?: string };

export function useNav() {
  // Nav state ← URL search (TanStack Router). l/s stay as csv strings in the URL; the override params
  // (catalog, m, ftsdb, …) ride in the same search untouched (see router.tsx validateSearch).
  const sp = useSearch({ from: "__root__" });
  const navigate = useNavigate();
  // The view is the PATH, not a ?view= param — see routes.tsx for why. "/" is Discover.
  const pathname = useRouterState({ select: (st) => st.location.pathname });
  const seg = pathname.replace(/^\/+|\/+$/g, "").split("/")[0];
  const view: View = isView(seg) ? seg : "discover";
  const collectionUrl = sp.c;
  const itemUrl = sp.i;
  const urlLayerIds = parseLayerParam(sp.l);
  const seriesSel = sp.s ? sp.s.split(",").filter(Boolean) : undefined;

  // Navigate by setting the nav search params; override/spike params are preserved. push for user nav
  // (back/forward works via the router), replace for programmatic syncs. The Discover-owned keys
  // (q/collections/category/…) are stripped when leaving Discover so its filters don't linger on
  // another view, and preserved when staying in Discover (open/close a drawer over the filtered set).
  const go = (next: Nav, push = true) => {
    if (next.view !== "map") setPin(null);   // the search's place pin belongs to this map visit
    navigate({
      to: VIEW_PATH[next.view],
      replace: !push,
      search: (prev) => {
        const { view: _v, c: _c, i: _i, l: _l, s: _s, sheet, terrain, ...rest } = prev;
        // Preview is only ever reached from Discover, so carry the filter state through it → Back
        // restores the filtered result set the user came from.
        const stay = next.view === "discover" || next.view === "preview";
        return {
          ...(stay ? rest : withoutDiscover(rest)),
          sheet: next.view === "map" ? next.sheet ?? sheet : undefined,   // the map's own; it doesn't follow you out
          terrain: next.i && next.i === prev.i ? terrain : undefined,     // a new item opens in 2D
          c: next.c || undefined,
          i: next.i || undefined,
          l: layerParam(next.l),
          s: next.s?.length ? next.s.join(",") : undefined,
        };
      },
    });
  };
  // The header search hands a query to Discover, clearing stale Discover keys. `to` is REQUIRED: a
  // view is a path, so navigating with search alone would stay put.
  const openDiscoverSearch = (opts: { q?: string; category?: string }) =>
    navigate({
      to: VIEW_PATH.discover,
      search: (prev) => {
        const { view: _v, c: _c, i: _i, l: _l, s: _s, sheet: _sh, ...rest } = prev;
        return { ...withoutDiscover(rest), q: opts.q || undefined, category: opts.category || undefined };
      },
    });
  return { sp, view, collectionUrl, itemUrl, urlLayerIds, seriesSel, go, openDiscoverSearch };
}
