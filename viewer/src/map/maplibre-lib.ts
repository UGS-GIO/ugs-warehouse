// maplibre-gl 6 is ESM-only and loads its worker from a URL. Vite must emit the worker as its own
// self-contained chunk (`?worker&url`), so the maps import maplibre from here, which sets that URL.
import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

maplibregl.setWorkerUrl(workerUrl);

export default maplibregl;
