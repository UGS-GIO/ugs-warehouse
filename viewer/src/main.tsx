import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import React from "react";
import ReactDOM from "react-dom/client";
import { CappedMap } from "./lru";
import { router } from "./router";
import { applyTheme, initialTheme } from "./theme";
import "@utahdts/utah-design-system-header/css";
import "./index.css";
import "maplibre-gl/dist/maplibre-gl.css";

// Apply persisted theme before first paint to avoid a flash.
applyTheme(initialTheme());

// Register the pmtiles:// protocol once (module load) so react-map-gl can read PMTiles.
// The Protocol caches one PMTiles per archive URL in a plain `tiles` Map for the tab's life; cap it
// (well above the real layer count, so normal browsing never evicts) so a runaway can't grow unbounded.
const PMTILES_ARCHIVE_CAP = 32;
const protocol = new Protocol();
protocol.tiles = new CappedMap(PMTILES_ARCHIVE_CAP);
maplibregl.addProtocol("pmtiles", protocol.tile);

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
