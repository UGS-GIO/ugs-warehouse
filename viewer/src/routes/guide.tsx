import { createFileRoute } from "@tanstack/react-router";

import { useViewCtx } from "@/app";
import { Developers } from "@/shell/developers-view";
import { Guide } from "@/shell/guide";
import { CATALOG_URL } from "@/stac";

function GuidePage() {
  const { rootChildren } = useViewCtx();
  return <Guide developers={<Developers catalogUrl={CATALOG_URL} groups={rootChildren} />} />;
}

export const Route = createFileRoute("/guide")({ component: GuidePage });
