import { createFileRoute } from "@tanstack/react-router";

import { useViewCtx } from "../app";
import { Developers } from "../developers-view";
import { CATALOG_URL } from "../stac";

function DevelopersPage() {
  const { rootChildren } = useViewCtx();
  return <Developers catalogUrl={CATALOG_URL} groups={rootChildren} />;
}

export const Route = createFileRoute("/developers")({ component: DevelopersPage });
