// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDebounced } from "./use-debounced";

function Probe({ value }: { value: string }) {
  return <span data-testid="out">{useDebounced(value, 300)}</span>;
}

describe("useDebounced", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("shows the first value immediately, then settles on the last one", () => {
    const { rerender } = render(<Probe value="a" />);
    expect(screen.getByTestId("out").textContent).toBe("a");

    rerender(<Probe value="ab" />);
    rerender(<Probe value="abc" />);
    // Mid-flight: still the old value, and the intermediate keystroke never lands.
    act(() => { vi.advanceTimersByTime(299); });
    expect(screen.getByTestId("out").textContent).toBe("a");

    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.getByTestId("out").textContent).toBe("abc");
  });
});
