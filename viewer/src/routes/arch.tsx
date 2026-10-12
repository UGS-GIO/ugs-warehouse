import { createFileRoute, redirect } from "@tanstack/react-router";

// The architecture page lives on the docs site now; old /arch links land on it.
export const Route = createFileRoute("/arch")({
  beforeLoad: () => { throw redirect({ href: "https://maps-assets.geology.utah.gov/warehouse/docs/ARCHITECTURE/index.html" }); },
});
