import { createFileRoute, redirect } from "@tanstack/react-router";

// The developer reference is the end of the Guide now; old /developers links land on it.
export const Route = createFileRoute("/developers")({
  beforeLoad: () => { throw redirect({ to: "/guide", hash: "for-developers", replace: true }); },
});
