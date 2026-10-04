// @vitest-environment jsdom
import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { queryClient } from "./query-client";

// The defaults are app-wide, so test what they DO: a query that sets no options of its own must not
// refetch when a component remounts inside the freshness window, and must not retry a failure three
// times over. Both were the pre-default behaviour.
function Probe({ queryFn }: { queryFn: () => Promise<string> }) {
  const { data } = useQuery({ queryKey: ["probe"], queryFn });
  return <span>{data ?? "…"}</span>;
}

const show = (queryFn: () => Promise<string>) =>
  render(<QueryClientProvider client={queryClient}><Probe queryFn={queryFn} /></QueryClientProvider>);

describe("query client defaults", () => {
  it("does not refetch on remount inside the stale window", async () => {
    const queryFn = vi.fn(async () => "ok");
    const first = show(queryFn);
    await waitFor(() => expect(queryFn).toHaveBeenCalledTimes(1));
    first.unmount();

    show(queryFn).unmount();
    await waitFor(() => expect(queryFn).toHaveBeenCalledTimes(1));
    queryClient.clear();
  });

  it("retries a failure once, not three times", async () => {
    const queryFn = vi.fn(async () => { throw new Error("404"); });
    show(queryFn);

    await waitFor(() => expect(queryFn).toHaveBeenCalledTimes(2), { timeout: 5_000 });
    // Settle: a third call would mean the default retry count is back.
    await new Promise((r) => setTimeout(r, 50));
    expect(queryFn).toHaveBeenCalledTimes(2);
    queryClient.clear();
  });

  it("leaves window-focus refetching on, so a stale review thread comes back current", () => {
    expect(queryClient.getDefaultOptions().queries?.refetchOnWindowFocus).toBeUndefined();
  });
});
