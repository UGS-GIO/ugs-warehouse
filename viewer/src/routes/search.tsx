import { createFileRoute } from "@tanstack/react-router";

import { useViewCtx } from "../app";
import { ArticleSearch } from "../search";

function Search() {
  const { catalogDocs, go } = useViewCtx();
  return <ArticleSearch catalog={catalogDocs}
    onOpen={(collId, itemId) => go({ view: "catalog", c: collId, i: itemId })} />;
}

export const Route = createFileRoute("/search")({ component: Search });
