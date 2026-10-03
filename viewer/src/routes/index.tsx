import { createFileRoute, redirect } from "@tanstack/react-router";

// "/" is Discover: its idle state is the front door (category index over the newest items). A legacy
// "/?c=&i=" link still opens the catalog item it always did.
export const Route = createFileRoute("/")({
  beforeLoad: ({ search }) => {
    throw redirect({ to: search.c || search.i ? "/catalog" : "/discover", search, replace: true });
  },
});
