// The content views: no shared selection state beyond navigation.
import { Architecture, ArticleSearch, Developers, Guide, ReviewDashboard, useViewCtx } from "../app";
import { CATALOG_URL } from "../stac";

export const GuideRoute = () => <Guide />;

export const ArchRoute = () => <Architecture />;

export const DevelopersRoute = () => {
  const { rootChildren } = useViewCtx();
  return <Developers catalogUrl={CATALOG_URL} groups={rootChildren} />;
};

export const SearchRoute = () => {
  const { catalogDocs, go } = useViewCtx();
  return <ArticleSearch catalog={catalogDocs}
    onOpen={(collId, itemId) => go({ view: "catalog", c: collId, i: itemId })} />;
};

// Review data are vector serving-topics → the ugs-serving-topics collection. Open the item there.
export const ReviewRoute = () => {
  const { go } = useViewCtx();
  return <ReviewDashboard onOpen={(itemId) => go({ view: "catalog", c: "ugs-serving-topics", i: itemId })} />;
};
