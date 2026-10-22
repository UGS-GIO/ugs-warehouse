// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Breadcrumb } from "./breadcrumb";

describe("Breadcrumb", () => {
  it("links the crumbs with a handler, ends on the current page, and takes the caller's class", async () => {
    const onClick = vi.fn();
    const { container } = render(<Breadcrumb className="mb-1.5 text-xs"
      crumbs={[{ label: "Catalog", onClick }, { label: "Publications" }, { label: "OFR-784DM" }]} />);
    expect(container.firstElementChild?.className).toBe("mb-1.5 text-xs");
    expect(container.textContent).toBe("Catalog / Publications / OFR-784DM");
    await userEvent.click(screen.getByText("Catalog"));
    expect(onClick).toHaveBeenCalledOnce();
    expect(screen.getByText("OFR-784DM").className).toBe("");
  });

  it("offers up to the crumb before the current page, by click and by Alt+ArrowUp", async () => {
    const up = vi.fn();
    render(<Breadcrumb crumbs={[{ label: "Catalog", onClick: vi.fn() }, { label: "Publications", onClick: up },
      { label: "Open File Report" }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Up to Publications" }));
    expect(up).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Alt>}{ArrowUp}{/Alt}");
    expect(up).toHaveBeenCalledTimes(2);
    await userEvent.keyboard("{ArrowUp}");
    expect(up).toHaveBeenCalledTimes(2);
  });

  it("makes a crumb a focusable button that Enter activates", async () => {
    const onClick = vi.fn();
    render(<Breadcrumb crumbs={[{ label: "Catalog", onClick }, { label: "Publications" }]} />);
    await userEvent.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Catalog" }));
    await userEvent.keyboard("{Enter}");
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("leaves Alt+ArrowUp alone while typing in a field or over the map", async () => {
    const up = vi.fn();
    render(<div>
      <Breadcrumb crumbs={[{ label: "Catalog", onClick: vi.fn() }, { label: "Publications", onClick: up }, { label: "OFR" }]} />
      <input aria-label="q" /><textarea aria-label="t" /><div contentEditable suppressContentEditableWarning data-testid="ce" />
      <div className="maplibregl-map"><canvas data-testid="map" /></div>
    </div>);
    for (const el of [screen.getByLabelText("q"), screen.getByLabelText("t"), screen.getByTestId("ce"), screen.getByTestId("map")]) {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", altKey: true, bubbles: true, cancelable: true }));
    }
    expect(up).not.toHaveBeenCalled();
  });

  it("has no up control at the root or when the parent is not a link", () => {
    render(<Breadcrumb crumbs={[{ label: "Catalog" }]} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
