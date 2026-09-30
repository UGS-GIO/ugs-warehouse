// What the export pre-flight found, as a flat list the warning box renders without knowing each check.
import type { ReactNode } from "react";

import type { ShapefileWarnings } from "@/data/download";
import { SLOW_READ_BYTES } from "@/data/download";
import { type ExportFormat, FORMAT_LABEL } from "@/data/export-formats";

const fmtBytes = (n: number) =>
  n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`;

// Worst first: the heading names the worst level present.
export type FindingLevel = "too-big" | "mangle" | "slow";

export interface Finding {
  id: string;
  level: FindingLevel;
  title: ReactNode;
  detail?: ReactNode;
  noForce?: boolean;   // forcing past it produces no file, so "Download anyway" is withheld
}

export function exportFindings(w: ShapefileWarnings, fmt: ExportFormat): Finding[] {
  const out: Finding[] = [];
  const label = FORMAT_LABEL[fmt];
  if (w.mixedGeometry.length) out.push({
    id: "mixed", level: "mangle", noForce: true,
    title: "Mixed geometry",
    detail: <>({w.mixedGeometry.join(", ").toLowerCase()}) — {label} holds one geometry type per
      layer, so the conversion fails rather than dropping the others. Use GeoPackage or
      FlatGeobuf, which take all of them.</>,
  });
  if (w.longNames.length) out.push({
    id: "long-names", level: "mangle",
    title: `${w.longNames.length} field name${w.longNames.length === 1 ? "" : "s"} over 10 chars`,
    detail: <>get truncated (e.g. <code>{w.longNames[0]}</code> → <code>{w.longNames[0].slice(0, 10)}</code>).</>,
  });
  if (w.collisions.length) out.push({
    id: "collisions", level: "mangle",
    title: "Field-name collisions",
    detail: <>after truncation — <code>{w.collisions[0][0]}</code> &amp; <code>{w.collisions[0][1]}</code> collapse
      to the same name (data loss).</>,
  });
  if (w.tooManyFields) out.push({
    id: "too-many-fields", level: "mangle",
    title: `${w.fieldCount} fields`, detail: "exceeds the 255-field shapefile limit.",
  });
  if (w.overBrowserLimit) out.push({
    id: "browser-limit", level: "too-big", noForce: true,
    title: "Too big to convert in the browser",
    detail: <>— {w.rowCount.toLocaleString()} features need about {fmtBytes(w.estPeakBytes)} of
      memory and the tab has roughly 1.5 GB. Clip to a smaller area: only the parts of the file
      that area covers are read. Otherwise download the GeoParquet above and convert it locally
      (QGIS, GDAL).</>,
  });
  if (w.estReadBytes > SLOW_READ_BYTES) out.push({
    id: "slow-read", level: "slow",
    title: `Reads ${fmtBytes(w.estReadBytes)} first`,
    detail: w.minClipBytes > SLOW_READ_BYTES
      ? `before writing anything. This file is stored in ${w.rowGroups} block${w.rowGroups === 1 ? "" : "s"}`
        + ` of up to ${fmtBytes(w.minClipBytes)}, and a block is the smallest piece`
        + " that can be skipped, so clipping cannot bring it much below that."
      : "before writing anything. Clip to a smaller area to read less.",
  });
  if (w.widthsEstimated) out.push({
    id: "widths-estimated", level: "slow",
    title: "Sizes are approximate",
    detail: "— this layer has too much text to measure exactly without reading it, so the"
      + " estimates above use average field lengths.",
  });
  if (w.over2gb) out.push({
    id: "over-2gb", level: "mangle",
    title: "Over the 2 GB per-file shapefile limit",
    detail: `— estimated ${fmtBytes(w.estShpBytes)} of geometry (.shp) and`
      + ` ${fmtBytes(w.estDbfBytes)} of attributes (.dbf). Use GeoPackage, or clip to a smaller area.`,
  });
  return out;
}

const HEADINGS: Record<FindingLevel, (label: string) => string> = {
  "too-big": () => "This export is too big for the browser",
  mangle: (label) => `${label} will mangle this data`,
  slow: () => "This export will be slow",
};
const ORDER: FindingLevel[] = ["too-big", "mangle", "slow"];

export const findingsHeading = (fs: Finding[], fmt: ExportFormat) =>
  HEADINGS[ORDER.find((l) => fs.some((f) => f.level === l)) ?? "slow"](FORMAT_LABEL[fmt]);

// Carries its own format, so rendering it never reaches back into the mutation's variables.
export type Warning = { fmt: ExportFormat; findings: Finding[] };
