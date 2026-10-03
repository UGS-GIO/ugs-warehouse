// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useScrollOnNew } from "./use-scroll-on-new";

const scrollIntoView = vi.fn();

function Probe({ sel }: { sel: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  useScrollOnNew(sel, ref);
  return <div ref={ref} />;
}

describe("useScrollOnNew", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    scrollIntoView.mockClear();
    Element.prototype.scrollIntoView = scrollIntoView;
  });
  afterEach(() => vi.useRealTimers());

  const flush = () => vi.advanceTimersByTime(1);

  it("scrolls when a selection arrives, and again only when it changes", () => {
    const { rerender } = render(<Probe sel={null} />);
    flush();
    expect(scrollIntoView).not.toHaveBeenCalled();

    rerender(<Probe sel="a" />);
    flush();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);

    // Same selection re-rendered (a sibling changed) — no second scroll.
    rerender(<Probe sel="a" />);
    flush();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);

    rerender(<Probe sel="b" />);
    flush();
    expect(scrollIntoView).toHaveBeenCalledTimes(2);
  });

  it("does not scroll when a selection is already held at mount", () => {
    // The page↔drawer layout switch: the panel remounts carrying the previous selection.
    render(<Probe sel="a" />);
    flush();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
