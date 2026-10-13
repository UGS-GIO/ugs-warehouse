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
});
