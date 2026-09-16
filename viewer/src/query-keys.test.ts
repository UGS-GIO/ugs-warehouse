import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { qk } from "./query-keys";

const invalidated = (qc: QueryClient, key: readonly unknown[]) =>
  Boolean(qc.getQueryState(key)?.isInvalidated);

describe("comment query keys", () => {
  // The prefixes are the point: a mutation invalidates a broad key and every narrower thread under
  // it has to go with it. These assert the real matcher, not the shape.
  it("invalidating an item's comments covers every thread on it", () => {
    const qc = new QueryClient();
    const item = qk.comments.item("hazards_qfaults");
    const row = qk.comments.thread("hazards_qfaults", { kind: "row", rowVal: "42" });
    const col = qk.comments.thread("hazards_qfaults", { kind: "column", column: "depth" });
    const other = qk.comments.thread("emp_ucrc_wells", { kind: "item" });
    for (const key of [item, row, col, other]) qc.setQueryData(key, []);

    qc.invalidateQueries({ queryKey: item });

    expect([invalidated(qc, row), invalidated(qc, col)]).toEqual([true, true]);
    expect(invalidated(qc, other)).toBe(false);
  });

  it("invalidating all comments covers the report", () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.comments.report, []);

    qc.invalidateQueries({ queryKey: qk.comments.all });

    expect(invalidated(qc, qk.comments.report)).toBe(true);
  });
});
