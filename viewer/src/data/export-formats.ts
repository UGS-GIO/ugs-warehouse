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

const ORDER: ExportFormat[] = ["shp", "gpkg", "gdb", "fgb", "geojson", "csv"];

export const FORMATS = ORDER.map((id) => ({ id, label: FORMAT_LABEL[id] }));
