// Kept out of `download.ts` so drawing the buttons doesn't pull the exporter into the entry chunk.
export type ExportFormat = "shp" | "gpkg" | "gdb" | "fgb" | "geojson" | "csv";

// Record over the union: adding a format without a label fails the type check.
export const FORMAT_LABEL: Record<ExportFormat, string> = {
  shp: "Shapefile (zip)",
  gpkg: "GeoPackage",
  gdb: "File Geodatabase (zip)",
  fgb: "FlatGeobuf",
  geojson: "GeoJSON",
  csv: "CSV (WKT)",
};

// What each export is, by file, without naming an application.
const FORMAT_HINT: Record<ExportFormat, string> = {
  shp: ".shp in a zip",
  gpkg: ".gpkg",
  gdb: ".gdb in a zip",
  fgb: ".fgb",
  geojson: ".geojson · always WGS 84",
  csv: ".csv · geometry as WKT",
};

const ORDER: ExportFormat[] = ["shp", "gpkg", "gdb", "fgb", "geojson", "csv"];

export const FORMATS = ORDER.map((id) => ({ id, label: FORMAT_LABEL[id] }));

// The export buttons for an item: every format for spatial data. A plain table has no geometry to
// write, so it gets CSV alone, with no WKT column.
export const exportFormatsFor = (spatial: boolean): { id: ExportFormat; label: string; hint: string }[] =>
  spatial
    ? FORMATS.map((f) => ({ ...f, hint: FORMAT_HINT[f.id] }))
    : [{ id: "csv", label: "CSV", hint: ".csv" }];
