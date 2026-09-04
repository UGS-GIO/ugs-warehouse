import { createFileRoute } from "@tanstack/react-router";

import { idOf, useViewCtx } from "../app";
import { Browse } from "../browse";

function Catalog() {
const c = useViewCtx();
return <Browse
  cards={c.cardsWithCovers}
  collectionId={c.collectionId}
  allItems={c.allItems}
  itemsLoading={c.itemsLoading}
  showItems={Boolean(c.leafColl)}
  atRoot={!c.collectionId}
  breadcrumb={c.crumbs}
  search={c.search}
  onSearch={c.setSearch}
  threeD={c.threeD}
  onThreeD={c.setThreeD}
  browseAll={c.browseAll}
  onBrowseAll={c.setBrowseAll}
  layerCollectionIds={c.layerCollIds}
  series={c.seriesSel ?? []}
  onSeries={c.setSeries}
  item={c.item.data}
  itemSelected={Boolean(c.itemUrl)}
  onOpenCollection={c.openCollection}
  onOpenItem={c.openItem}
  onOpenCover={c.openCover}
  onBackToItems={() => c.go({ view: "catalog", c: c.collectionUrl, s: c.seriesSel })}
  onViewMap={() => c.go({ view: "map", c: c.collectionUrl, i: c.itemUrl, l: c.itemUrl ? [idOf(c.itemUrl)] : c.layerIds })} />;
}

export const Route = createFileRoute("/catalog")({ component: Catalog });
