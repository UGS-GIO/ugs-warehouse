// The data table's plain view, read with hyparquet: a ~10 KB JavaScript parquet reader.
//
// Paging through a table in file order needs only the footer and the requested rows' column
// chunks, read by HTTP range. DuckDB-WASM did the same reads but started a whole database engine
// (a 36 MB WebAssembly module) on every layer page to do it, which froze and crashed phones.
// DuckDB still answers what needs a query engine: free-text search, column filters and sorting
// (data-explorer.tsx picks the path).
import { asyncBufferFromUrl, cachedAsyncBuffer, type AsyncBuffer, type FileMetaData,
  parquetMetadataAsync, parquetReadObjects, parquetSchema, type SchemaElement } from "hyparquet";
import { compressors } from "hyparquet-compressors";
import { CappedMap } from "@/lib/lru";
import { BBOX_COLS, GEOM_NAMES, ID_COL, sanitize } from "./columns";
import type { ColType, Page } from "./download";

type Opened = { file: AsyncBuffer; metadata: FileMetaData; columns: SchemaElement[] };

// A few recent files stay open, so paging back and forth re-reads no footer.
const opened = new CappedMap<string, Promise<Opened>>(8);

function open(url: string): Promise<Opened> {
  let o = opened.get(url);
  if (!o) {
    o = (async () => {
      const file = cachedAsyncBuffer(await asyncBufferFromUrl({ url }));
      const metadata = await parquetMetadataAsync(file);
      return { file, metadata, columns: parquetSchema(metadata).children.map((c) => c.element) };
    })();
    // A failed open is not kept, or one network blip would break the table until reload.
    o.catch(() => opened.delete(url));
    opened.set(url, o);
  }
  return o;
}

// Numeric (range filter) vs everything else (substring), matching download.ts's DuckDB mapping:
// integers, floats and decimals are numbers; dates, times and timestamps read as text.
// Writers mark these with the logical type, the older converted type, or both (DuckDB writes a date
// with the converted type alone).
const TEXTUAL = new Set(["DATE", "TIME", "TIMESTAMP", "STRING", "JSON", "UUID", "ENUM"]);
const TEXTUAL_CONVERTED = new Set(["DATE", "TIME_MILLIS", "TIME_MICROS", "TIMESTAMP_MILLIS", "TIMESTAMP_MICROS", "UTF8", "JSON", "ENUM"]);
function colType(e: SchemaElement): ColType {
  if (e.logical_type && TEXTUAL.has(e.logical_type.type)) return "text";
  if (e.converted_type && TEXTUAL_CONVERTED.has(e.converted_type)) return "text";
  if (e.logical_type?.type === "DECIMAL" || e.converted_type === "DECIMAL") return "number";
  return e.type === "INT32" || e.type === "INT64" || e.type === "FLOAT" || e.type === "DOUBLE" ? "number" : "text";
}

/** Which columns show, and which ride along hidden (bbox for row→map zoom, feature_id for linking). */
function layout(columns: SchemaElement[]) {
  const names = columns.map((c) => c.name);
  const geom = new Set(GEOM_NAMES.filter((g) => names.includes(g)));
  const hasBbox = BBOX_COLS.every((c) => names.includes(c));
  const hidden = new Set([...geom, ...(hasBbox ? BBOX_COLS : []), ID_COL]);
  const shown = columns.filter((c) => !hidden.has(c.name));
  const read = names.filter((n) => !geom.has(n));   // never the geometry: the map draws that
  const types: Record<string, ColType> = Object.fromEntries(shown.map((c) => [c.name, colType(c)]));
  return { shown: shown.map((c) => c.name), read, hasBbox, types };
}

// Chunks this close together are fetched as one range: the bytes between cost less than a request.
const MERGE_GAP = 64 * 1024;

/**
 * The file, with the column chunks a read will need fetched up front as a few merged ranges.
 * hyparquet asks for each column chunk separately, and a page of a 35-column table was 35 range
 * requests (4.4 s, against the 265 KB they carried); the chunks of one row group sit next to each
 * other, so they come back as one or two.
 */
function prefetched(o: Opened, columns: string[], rowStart: number, rowEnd: number): AsyncBuffer {
  const wanted = new Set(columns);
  const spans: [number, number][] = [];
  let groupStart = 0;
  for (const group of o.metadata.row_groups) {
    const groupEnd = groupStart + Number(group.num_rows);
    if (groupEnd > rowStart && groupStart < rowEnd) {
      for (const chunk of group.columns) {
        const m = chunk.meta_data;
        if (!m || !wanted.has(m.path_in_schema[0])) continue;
        const start = Number(m.dictionary_page_offset ?? m.data_page_offset);
        spans.push([start, start + Number(m.total_compressed_size)]);
      }
    }
    groupStart = groupEnd;
  }
  spans.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const [start, end] of spans) {
    const last = merged.at(-1);
    if (last && start - last[1] <= MERGE_GAP) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  const fetched = merged.map(([start, end]) => ({ start, end, bytes: Promise.resolve(o.file.slice(start, end)) }));
  return {
    byteLength: o.file.byteLength,
    slice(start: number, end = o.file.byteLength) {
      const hit = fetched.find((f) => f.start <= start && end <= f.end);
      return hit ? hit.bytes.then((b) => b.slice(start - hit.start, end - hit.start)) : o.file.slice(start, end);
    },
  };
}

/** Displayed column → filter kind, from the footer alone. */
export async function columnTypes(url: string): Promise<Record<string, ColType>> {
  return layout((await open(url)).columns).types;
}

function toPage(rows: Record<string, unknown>[], total: number, l: ReturnType<typeof layout>): Page {
  const clean = rows.map((r) => {
    const out: Record<string, unknown> = {};
    for (const c of [...l.shown, ID_COL]) if (c in r) out[c] = sanitize(r[c]);
    return out;
  });
  const bboxes = rows.map((r): [number, number, number, number] | null => {
    if (!l.hasBbox) return null;
    const [xmin, ymin, xmax, ymax] = BBOX_COLS.map((c) => Number(r[c]));
    return [xmin, ymin, xmax, ymax].every(Number.isFinite) ? [xmin, ymin, xmax, ymax] : null;
  });
  return { columns: l.shown, types: l.types, rows: clean, total, bboxes };
}

/** One page of rows in file order: `limit` rows from `offset`. Reads only their row groups. */
export async function readPage(url: string, { limit, offset }: { limit: number; offset: number }): Promise<Page> {
  const o = await open(url);
  const l = layout(o.columns);
  const total = Number(o.metadata.num_rows);
  const rowEnd = Math.min(total, offset + limit);
  const rows = offset >= total ? [] : await parquetReadObjects({
    file: prefetched(o, l.read, offset, rowEnd), metadata: o.metadata, compressors, columns: l.read, rowStart: offset, rowEnd,
  });
  return toPage(rows, total, l);
}

/** Row positions (file order) whose `col` equals `value`, compared as text as DuckDB's path does. */
async function matching(o: Opened, col: string, value: string): Promise<number[]> {
  const rows = await parquetReadObjects({ file: o.file, metadata: o.metadata, compressors, columns: [col] });
  const out: number[] = [];
  rows.forEach((r, i) => { if (String(sanitize(r[col])) === value) out.push(i); });
  return out;
}

/**
 * One page of the rows where `col` equals `value`: the related-rows view (a clicked feature's
 * children). Reads the key column once, then only the span of rows the page needs.
 */
export async function readMatching(url: string, col: string, value: string,
  { limit, offset }: { limit: number; offset: number }): Promise<Page> {
  const o = await open(url);
  const l = layout(o.columns);
  const all = await matching(o, col, value);
  const hits = all.slice(offset, offset + limit);
  if (!hits.length) return toPage([], all.length, l);
  const first = hits[0];
  const rowEnd = hits[hits.length - 1] + 1;
  const span = await parquetReadObjects({
    file: prefetched(o, l.read, first, rowEnd), metadata: o.metadata, compressors, columns: l.read, rowStart: first, rowEnd,
  });
  return toPage(hits.map((i) => span[i - first]), all.length, l);
}

/**
 * Position of the row carrying `featureId` in the view the table shows, or null: pages the table
 * to a map click. File order, or, with a related-rows match, order among the matching rows.
 */
export async function ordinalOf(url: string, featureId: number, only?: { col: string; value: string }): Promise<number | null> {
  const o = await open(url);
  if (!o.columns.some((c) => c.name === ID_COL)) return null;
  const row = (await matching(o, ID_COL, String(featureId)))[0];
  if (row === undefined) return null;
  if (!only) return row;
  const pos = (await matching(o, only.col, only.value)).indexOf(row);
  return pos < 0 ? null : pos;
}
