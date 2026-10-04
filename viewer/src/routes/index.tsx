import { createFileRoute, redirect } from "@tanstack/react-router";

// "/" opens Discover. A legacy "/?c=&i=" link still opens the catalog item it always did.
export const Route = createFileRoute("/")({
  beforeLoad: ({ search }) => {
    throw redirect({ to: search.c || search.i ? "/catalog" : "/discover", search, replace: true });
  },
});
