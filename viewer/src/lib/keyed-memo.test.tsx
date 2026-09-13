// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { indexRowsKey, useKeyedMemo } from "./keyed-memo";

const row = (id: string, n: number) => ({ id, index: { items: Array.from({ length: n }, (_, i) => i) } });

describe("indexRowsKey", () => {
  it("moves when a collection's item count changes", () => {
    expect(indexRowsKey([row("pubs", 2)])).not.toBe(indexRowsKey([row("pubs", 3)]));
  });

  it("moves when a collection arrives or leaves", () => {
    expect(indexRowsKey([row("pubs", 2)])).not.toBe(indexRowsKey([row("pubs", 2), row("topics", 1)]));
  });

  it("is stable for the same rows rebuilt", () => {
    expect(indexRowsKey([row("pubs", 2), row("topics", 1)]))
      .toBe(indexRowsKey([row("pubs", 2), row("topics", 1)]));
  });

  it("counts a collection whose index has not loaded as zero, not as absent", () => {
    expect(indexRowsKey([{ id: "pubs" }])).toBe("pubs:0");
  });
});

describe("useKeyedMemo", () => {
  it("recomputes on a new key and not on an unrelated re-render", () => {
    const fn = vi.fn(() => ({}));
    const Probe = ({ k, other }: { k: string; other: number }) => {
      useKeyedMemo(k, fn);
      return <span>{other}</span>;
    };

    const { rerender } = render(<Probe k="a" other={1} />);
    rerender(<Probe k="a" other={2} />);
    expect(fn).toHaveBeenCalledTimes(1);

    rerender(<Probe k="b" other={2} />);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
