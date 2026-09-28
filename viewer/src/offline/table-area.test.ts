import { describe, expect, it } from "vitest";
import { clipClause, scanOf } from "@/data/download";
import { planTableBlocks, type TableGroup } from "./table-area";

// Big enough that DuckDB's 64 KiB tail read is not the whole file.
const SIZE = 1_000_000;

const B = 100;   // a small block, so the arithmetic reads plainly
const group = (x: number, start: number, end: number): TableGroup => ({ xmin: x, xmax: x + 1, ymin: 0, ymax: 1, start, end });
// Three groups side by side at x = 0, 10, 20, filling bytes 4..904.
const groups = [group(0, 4, 304), group(10, 304, 604), group(20, 604, 904)];
const TAIL = Array.from({ length: 656 }, (_, i) => 9344 + i);   // blocks of the last 64 KiB

describe("planTableBlocks", () => {
  it("keeps the header, the footer and only the row groups in the area", () => {
    const plan = planTableBlocks("t.parquet", SIZE, 80, groups, [9, 0, 12, 1], B);
    // header: block 0; group 2 (304..604): blocks 3-6; the tail DuckDB reads: its last 64 KiB
    expect(plan.blocks).toEqual([0, 3, 4, 5, 6, ...TAIL]);
    expect(plan.tiles).toBe(1);
    expect(plan.bbox).toEqual([9, 0, 12, 1]);
  });

  it("saves nothing but header and footer when no group is in the area", () => {
    const plan = planTableBlocks("t.parquet", SIZE, 80, groups, [50, 50, 60, 60], B);
    expect(plan.blocks).toEqual([0, ...TAIL]);
    expect(plan.tiles).toBe(0);
  });

  it("keeps a footer longer than DuckDB's tail read", () => {
    const plan = planTableBlocks("t.parquet", SIZE, 100_000, groups, [50, 50, 60, 60], B);
    expect(plan.blocks[1]).toBe(Math.floor((SIZE - 100_008) / B));
  });

  it("counts the last, short block by its real size", () => {
    const plan = planTableBlocks("t.parquet", 950, 10, groups, [50, 50, 60, 60], B);
    expect(plan.blocks).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);   // the whole small file is the tail
    expect(plan.bytes).toBe(950);
  });
});

describe("scanOf", () => {
  it("reads the file itself when there is no clip", () => {
    expect(scanOf("q1.parquet")).toEqual({ cte: "", from: "read_parquet('q1.parquet')" });
  });

  it("clips to each saved area in its own branch, without repeating a row", () => {
    const { cte, from } = scanOf("q1.parquet", [[0, 1, 2, 3], [4, 5, 6, 7]]);
    expect(from).toBe("clipped");
    expect(cte).toBe("WITH clipped AS MATERIALIZED ("
      + `SELECT * FROM read_parquet('q1.parquet') WHERE ${clipClause([0, 1, 2, 3])}`
      + ` UNION ALL SELECT * FROM read_parquet('q1.parquet') WHERE ${clipClause([4, 5, 6, 7])} AND NOT ${clipClause([0, 1, 2, 3])}) `);
  });
});
