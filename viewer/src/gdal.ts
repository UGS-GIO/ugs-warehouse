// Real GDAL/OGR in the browser (gdal3.js). DuckDB-WASM's GDAL output drivers are
// broken, but gdal3.js bundles a full GDAL build whose OGR drivers write correctly —
// including OpenFileGDB (Esri File Geodatabase, write support since GDAL 3.6). We feed
// it a GeoJSON (produced by DuckDB) and convert to GPKG / SHP / GDB / FlatGeobuf.
import { zipSync } from "fflate";
import initGdalJs from "gdal3.js";
import dataUrl from "gdal3.js/dist/package/gdal3WebAssembly.data?url";
import wasmUrl from "gdal3.js/dist/package/gdal3WebAssembly.wasm?url";

export type GdalTarget = { driver: string; ext: string; multi: boolean };

// OGR driver per output format. `multi` formats emit several files (shp sidecars,
// the .gdb directory) which we zip.
export const GDAL_TARGETS: Record<string, GdalTarget> = {
  gpkg: { driver: "GPKG", ext: "gpkg", multi: false },
  fgb: { driver: "FlatGeobuf", ext: "fgb", multi: false },
  shp: { driver: "ESRI Shapefile", ext: "shp", multi: true },
  gdb: { driver: "OpenFileGDB", ext: "gdb", multi: true },
};

type Gdal = Awaited<ReturnType<typeof initGdalJs>>;
let gdalPromise: Promise<Gdal> | null = null;

function getGdal(): Promise<Gdal> {
  if (!gdalPromise)
    gdalPromise = initGdalJs({
      paths: { wasm: wasmUrl, data: dataUrl },
      useWorker: false,
      // GDAL's non-fatal stderr (field-type coercion, name laundering) is warnings, not errors.
      errorHandler: (m: string) => console.warn(m),
    });
  return gdalPromise;
}

const basename = (p: string) => p.split("/").pop() ?? p;

/** Convert a GeoJSON string to `target` and return downloadable bytes + filename. */
export async function convertGeoJSON(
  geojson: string,
  stem: string,
  t: GdalTarget,
): Promise<{ bytes: Uint8Array; filename: string; mime: string }> {
  const gdal = await getGdal();
  const input = new File([geojson], "in.geojson", { type: "application/geo+json" });
  const { datasets } = await gdal.open(input);
  const ds = datasets[0];
  // Shapefile: pass the bare stem (the driver appends .shp/.dbf/… — giving `stem.shp`
  // would double to `stem.shp.shp`). Other drivers want the full filename.
  const outName = t.ext === "shp" ? stem : `${stem}.${t.ext}`;
  // -nln names the output layer after the topic (else it inherits "in" from in.geojson).
  const result = await gdal.ogr2ogr(ds, ["-f", t.driver, "-t_srs", "EPSG:4326", "-nln", stem], outName);

  if (!t.multi) {
    const bytes = await gdal.getFileBytes(result);
    return { bytes, filename: `${stem}.${t.ext}`, mime: "application/octet-stream" };
  }

  // Multi-file: collect the format's output files and zip. For a .gdb directory keep
  // the files nested under `<stem>.gdb/`; shapefile sidecars sit at the zip root.
  const outputs = await gdal.getOutputFiles();
  const entries: Record<string, Uint8Array> = {};
  for (const f of outputs) {
    const name = basename(f.path);
    if (t.ext === "gdb" && f.path.includes(`${stem}.gdb`)) entries[`${stem}.gdb/${name}`] = await gdal.getFileBytes(f.path);
    else if (t.ext === "shp" && name.startsWith(`${stem}.`)) entries[name] = await gdal.getFileBytes(f.path);
  }
  await gdal.close(ds);
  return { bytes: zipSync(entries), filename: `${stem}.${t.ext}.zip`, mime: "application/zip" };
}
