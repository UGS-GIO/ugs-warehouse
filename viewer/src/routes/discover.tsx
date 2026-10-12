import { createFileRoute, redirect } from "@tanstack/react-router";

// Discover is the home page now; old /discover links land on it.
export const Route = createFileRoute("/discover")({
  beforeLoad: ({ search }) => { throw redirect({ to: "/", search, replace: true }); },
});
