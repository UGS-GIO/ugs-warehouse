// Real GDAL/OGR in the browser (gdal3.js). DuckDB-WASM's GDAL output drivers are
// broken, but gdal3.js bundles a full GDAL build whose OGR drivers write correctly —
// including OpenFileGDB (Esri File Geodatabase, write support since GDAL 3.6). We feed
// it newline-delimited GeoJSON bytes (produced by DuckDB, read through OGR's GeoJSONSeq driver)
// and convert to GPKG / SHP / GDB / FlatGeobuf.
import { zip } from "fflate";
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
      // ogr2ogr blocks the thread it runs on, so a worker would be better. It does not work:
      // gdal3.js resolves its worker script relative to the page, which serves index.html, and it
      // postMessages its options, which cannot structured-clone the errorHandler function. Both
      // were tried in the browser and failed; recorded here so it is not re-litigated.
      useWorker: false,
      // GDAL's non-fatal stderr (field-type coercion, name laundering) is warnings, not errors.
      errorHandler: (m: string) => console.warn(m),
    });
  return gdalPromise;
}

const basename = (p: string) => p.split("/").pop() ?? p;

/** Convert GeoJSONSeq bytes (WGS84, one Feature per line) to `target`, reprojecting to `epsg`
 * (default 4326).
 * Float typing needs no help here: DuckDB's JSON writer keeps a whole-valued DOUBLE as `2.0`, so
 * OGR reads it as Real. (The JS `JSON.stringify` this replaced wrote `2`, which OGR read as
 * Integer; the OGR-SQL CAST that used to correct that rounded real decimals away, so it is gone.) */
export async function convertFeatureSeq(
  seq: Uint8Array,
  stem: string,
  t: GdalTarget,
  epsg = 4326,
): Promise<{ bytes: Uint8Array; filename: string; mime: string }> {
  const gdal = await getGdal();
  // The .geojsonl extension is what selects OGR's GeoJSONSeq driver; the layer is named "in".
  // Blob rejects a SharedArrayBuffer-backed view, and DuckDB's buffers can be one, so copy only
  // in that case rather than duplicating the whole payload on every export.
  const shared = typeof SharedArrayBuffer !== "undefined" && seq.buffer instanceof SharedArrayBuffer;
  // The check above is what rules out the shared case that Blob rejects.
  const body = shared ? new Uint8Array(seq) : (seq as Uint8Array<ArrayBuffer>);
  const input = new File([body], "in.geojsonl", { type: "application/geo+json-seq" });
  const { datasets } = await gdal.open(input);
  const ds = datasets[0];
  // Shapefile: pass the bare stem (the driver appends .shp/.dbf/… — giving `stem.shp`
  // would double to `stem.shp.shp`). Other drivers want the full filename.
  const outName = t.ext === "shp" ? stem : `${stem}.${t.ext}`;
  // -nln names the output layer after the topic (else it inherits "in" from in.geojsonl).
  // -t_srs reprojects from the GeoJSON's WGS84 to the user's chosen output CRS (e.g. 26912 UTM 12N).
  const args = ["-f", t.driver, "-t_srs", `EPSG:${epsg}`, "-nln", stem];
  const result = await gdal.ogr2ogr(ds, args, outName);

  // try/finally so the single-file early return still closes the dataset (else gpkg/fgb exports leak
  // the handle + MEMFS buffers into the GDAL runtime heap).
  try {
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
    // Async zip: zipSync blocks the main thread, and these archives run to hundreds of MB.
    const bytes = await new Promise<Uint8Array>((resolve, reject) =>
      zip(entries, (err, out) => (err ? reject(err) : resolve(out))));
    return { bytes, filename: `${stem}.${t.ext}.zip`, mime: "application/zip" };
  } finally {
    try { await gdal.close(ds); } catch { /* best-effort */ }
  }
}
