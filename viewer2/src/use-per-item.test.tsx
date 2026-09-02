// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { usePerItem } from "./use-per-item";

function Probe({ itemId }: { itemId: string }) {
  const [v, set] = usePerItem<string | null>(itemId, null);
  return (
    <>
      <output>{v ?? "none"}</output>
      <button onClick={() => set("picked")}>pick</button>
      <button onClick={() => set((prev) => `${prev ?? ""}+`)}>append</button>
    </>
  );
}

const shown = () => screen.getByRole("status").textContent;

describe("usePerItem", () => {
  it("drops the value the moment the item changes", async () => {
    const { rerender } = render(<Probe itemId="a" />);
    await userEvent.click(screen.getByText("pick"));
    expect(shown()).toBe("picked");

    // The assertion is that this is true on the FIRST render under the new id — a reset effect
    // would still be showing "picked" here, which is the frame this hook exists to prevent.
    rerender(<Probe itemId="b" />);
    expect(shown()).toBe("none");
  });

  it("does not hand the old value back when you return to the item", async () => {
    const { rerender } = render(<Probe itemId="a" />);
    await userEvent.click(screen.getByText("pick"));
    rerender(<Probe itemId="b" />);
    rerender(<Probe itemId="a" />);
    expect(shown()).toBe("none");
  });

  it("keeps the value across re-renders of the same item", async () => {
    const { rerender } = render(<Probe itemId="a" />);
    await userEvent.click(screen.getByText("pick"));
    rerender(<Probe itemId="a" />);
    expect(shown()).toBe("picked");
  });

  it("passes the current value to an updater", async () => {
    render(<Probe itemId="a" />);
    await userEvent.click(screen.getByText("pick"));
    await userEvent.click(screen.getByText("append"));
    expect(shown()).toBe("picked+");
  });

  // `onFeatureClick` uses the updater form, so an updater firing right after a switch must not
  // fold the previous item's value into the new one.
  it("starts an updater from `initial` after a switch", async () => {
    const { rerender } = render(<Probe itemId="a" />);
    await userEvent.click(screen.getByText("pick"));
    rerender(<Probe itemId="b" />);
    await userEvent.click(screen.getByText("append"));
    expect(shown()).toBe("+");
  });
});
