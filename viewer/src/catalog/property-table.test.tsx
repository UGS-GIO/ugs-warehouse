// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PropertyTable } from "./property-table";

describe("PropertyTable's datetime row", () => {
  it("names a publication's date Published, at the year it carries", () => {
    render(<PropertyTable properties={{ "ugs:series_id": "OFR-771", datetime: "2025-01-01T00:00:00Z" }} />);
    expect(screen.getByText("Published")).toBeTruthy();
    expect(screen.getByText("2025")).toBeTruthy();
    expect(screen.queryByText("datetime")).toBeNull();
  });

  it("names any other item's date Ingested", () => {
    render(<PropertyTable properties={{ "ugs:dbt_schema": "hazards", datetime: "2026-09-12T18:03:00Z" }} />);
    expect(screen.getByText("Ingested")).toBeTruthy();
    expect(screen.getByText("2026-09-12")).toBeTruthy();
  });
});
