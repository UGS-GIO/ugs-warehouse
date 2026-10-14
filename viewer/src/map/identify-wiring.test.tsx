// @vitest-environment jsdom
// The click handlers of the Map page and the item preview, with the map component stubbed: what a
// click on a geologic-map raster does next to vector features and the coverage popup.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import { forwardRef, useEffect, useImperativeHandle } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StacDoc } from "@/stac";

type Props = { onClick: (e: unknown) => void; onLoad?: () => void };
const h = vi.hoisted(() => ({ props: null as unknown, zoom: 10, units: [] as unknown[] }));
const latest = () => h.props as Props;

// A map whose zoom and identify-layer features the test sets.
const fakeMap = new Proxy({} as Record<string, unknown>, {
  get: (_t, k: string) => {
    if (k === "getZoom") return () => h.zoom;
    if (k === "getLayer") return () => ({});
    if (k === "queryRenderedFeatures") return (_g: unknown, o?: { layers?: string[] }) => (o?.layers?.some((l) => l.startsWith("idn-")) ? h.units : []);
    return () => undefined;
  },
});

vi.mock("react-map-gl/maplibre", () => {
  const Nothing = (): null => null;
  const MapGL = forwardRef<unknown, Props & { children?: React.ReactNode }>((props, ref): React.ReactElement => {
    h.props = props;
    useImperativeHandle(ref, () => ({ getMap: () => fakeMap, getZoom: () => h.zoom, fitBounds: () => {}, getContainer: () => document.body }));
    useEffect(() => { props.onLoad?.(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
    return <div>{props.children}</div>;
  });
  return {
    Map: MapGL, Source: Nothing, Layer: Nothing, Marker: Nothing, GeolocateControl: Nothing, NavigationControl: Nothing,
    Popup: ({ children }: { children: React.ReactNode }) => <div data-testid="popup">{children}</div>,
  };
});
vi.mock("@/map/maplibre-lib", () => ({ default: { addProtocol: () => {} } }));
vi.mock("maplibre-gl/dist/maplibre-gl.css", () => ({}));
vi.mock("./map-control", () => ({ MapControl: ({ children }: { children: React.ReactNode }) => <>{children}</> }));
vi.mock("./basemap-menu", () => ({ BasemapMenu: () => null }));
vi.mock("./pmtiles-protocol", () => ({ ensurePmtilesProtocol: () => {} }));
vi.mock("./cog", () => ({ ensureCogProtocol: async () => {} }));
vi.mock("@/offline/basemap-protocol", () => ({ basemapProtocol: () => {} }));
vi.mock("@/offline/store", () => ({ whenReady: async () => {} }));

import { ItemMap, type SelectedFeature } from "./map";
import PreviewMap from "./preview-map-gl";

vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  useSearch: () => false, useNavigate: () => () => {},
}));

const unit = { properties: { series_id: "M-1", unit_code: "Jm", unit_name: "Morrison", rank: 4 } };
const plate = { id: "plate", title: "Plate", cogHref: "https://x/p.cog.tif", bbox: [-113, 37, -109, 41], unitsHref: "https://x/u.pmtiles", pubTitle: "Geology" };
const footprints = [{ href: "https://x/f.json", id: "f", title: "Footprint item", bbox: [-113, 37, -109, 41] }];
const click = (features: unknown[] = []) => ({
  features, point: { x: 5, y: 5 }, lngLat: { lng: -111, lat: 39 }, target: fakeMap,
});

beforeEach(() => {
  h.zoom = 10; h.units = [];
  window.matchMedia = (() => ({ matches: false, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })) as never;
});

async function mountMap() {
  const picked: (SelectedFeature | null)[] = [];
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    render(<QueryClientProvider client={qc}>
      <ItemMap layers={[plate]} footprints={footprints} coverageDefault onSelectFeature={(f) => picked.push(f)} />
    </QueryClientProvider>);
  });
  return picked;
}

describe("Map page click", () => {
  it("identifies the unit under a raster click", async () => {
    const picked = await mountMap();
    h.units = [unit];
    act(() => latest().onClick(click()));
    expect(picked.at(-1)?.identify).toMatchObject({ kind: "unit", unit: { code: "Jm" } });
  });

  it("keeps a vector feature's own behavior", async () => {
    const picked = await mountMap();
    h.units = [unit];
    act(() => latest().onClick(click([{ layer: { id: "pm-x-fill" }, properties: { a: 1 } }])));
    expect(picked.at(-1)?.identify).toBeUndefined();
    expect(picked.at(-1)?.props).toEqual({ a: 1 });
  });

  it("opens the coverage popup, not scan-only, when a footprint is clicked with no unit", async () => {
    const picked = await mountMap();
    act(() => latest().onClick(click([{ layer: { id: "coverage-fill" }, properties: { title: "Footprint item", href: "https://x/f.json" } }])));
    expect(screen.getByTestId("popup").textContent).toContain("Open item");
    expect(picked.at(-1)).toBeNull();
  });

  it("answers scan only on a bare click on the raster", async () => {
    const picked = await mountMap();
    act(() => latest().onClick(click()));
    expect(picked.at(-1)?.identify).toEqual({ kind: "scan-only", pubTitle: "Geology" });
  });

  it("ignores a raster hidden by its zoom gate", async () => {
    h.zoom = 5;
    const picked = await mountMap();
    h.units = [unit];
    act(() => latest().onClick(click()));
    expect(picked.at(-1) ?? null).toBeNull();
  });
});

describe("item preview click", () => {
  const item = (pub: boolean): StacDoc => ({ type: "Feature", id: "plate", bbox: [-113, 37, -109, 41],
    properties: pub ? { title: "Plate", "ugs:in_publication": "M-1", "ugs:publication_title": "Geology" } : { title: "Plate" } } as StacDoc);
  const mount = async (spec: unknown) => {
    const got: unknown[] = [];
    const qc = new QueryClient();
    await act(async () => {
      render(<QueryClientProvider client={qc}>
        <PreviewMap spec={spec as never} slotEl={null} focus={null} onFeatureClick={() => {}} renders={{}} sel="" onSel={() => {}}
          onIdentify={(r) => got.push(r)} />
      </QueryClientProvider>);
    });
    return got;
  };

  it("reports the unit under a click on a plate with units", async () => {
    const got = await mount({ kind: "rasterpm", item: item(true), href: "https://x/m.pmtiles", unitsHref: "https://x/u.pmtiles" });
    h.units = [unit];
    act(() => latest().onClick(click()));
    expect(got.at(-1)).toMatchObject({ kind: "unit", unit: { code: "Jm" } });
  });

  it("says scan only for a publication plate without units, and stays silent for any other raster", async () => {
    const got = await mount({ kind: "rasterpm", item: item(true), href: "https://x/m.pmtiles" });
    act(() => latest().onClick(click()));
    expect(got.at(-1)).toEqual({ kind: "scan-only", pubTitle: "Geology" });
    cleanup();
    const other = await mount({ kind: "rasterpm", item: item(false), href: "https://x/m.pmtiles" });
    act(() => latest().onClick(click()));
    expect(other).toEqual([]);
  });
});
