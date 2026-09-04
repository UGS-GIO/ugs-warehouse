import { createFileRoute } from "@tanstack/react-router";

import { Developers, useViewCtx } from "../app";
import { CATALOG_URL } from "../stac";

function DevelopersPage() {
  const { rootChildren } = useViewCtx();
  return <Developers catalogUrl={CATALOG_URL} groups={rootChildren} />;
}

export const Route = createFileRoute("/developers")({ component: DevelopersPage });
