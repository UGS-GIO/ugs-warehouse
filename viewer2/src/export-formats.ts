// Kept out of `download.ts` so drawing the buttons doesn't pull the exporter into the entry chunk.
export type ExportFormat = "shp" | "gpkg" | "gdb" | "fgb" | "geojson" | "csv";

export const FORMATS: { id: ExportFormat; label: string }[] = [
  { id: "shp", label: "Shapefile (zip)" },
  { id: "gpkg", label: "GeoPackage" },
  { id: "gdb", label: "File Geodatabase (zip)" },
  { id: "fgb", label: "FlatGeobuf" },
  { id: "geojson", label: "GeoJSON" },
  { id: "csv", label: "CSV (WKT)" },
];
