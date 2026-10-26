import { createFileRoute } from "@tanstack/react-router";

import { idOf, useViewCtx } from "@/app";
import { DiscoveryView } from "@/discover/discovery-view";

// Discover is the home page. Full-width discovery split — same loaded item set as the map (less the
// publications' tagged layers and tables, one card per publication), its own facet rail / cards /
// synced map. Opening a card sets ?i= (staying here); the layout resolves the full doc → the drawer.
// The search index keeps the full map set, so a publication's layer is still findable there.
function Discover() {
const c = useViewCtx();
return <DiscoveryView
  items={c.discoverItems}
  searchItems={c.mapItems}
  itemsKey={c.mapLoadKey}
  onOpenItem={c.openItem}
  onOpenPub={(collId, itemId) => c.go({ view: "catalog", c: collId, i: itemId })}
  itemSelected={Boolean(c.itemUrl)}
  selectedItem={c.item.data}
  selectedItemError={c.item.error}
  selectedPartOf={c.partOf}
  selectedMissing={c.itemNotFound ? c.itemUrl : undefined}
  selectedCollectionId={c.collectionId}
  selectedCrumbs={c.crumbs}
  onCloseItem={() => c.go({ view: "discover" })}
  onViewOnMap={() => c.go({ view: "map", c: c.collectionUrl, i: c.itemUrl, l: c.itemUrl ? [idOf(c.itemUrl)] : c.layerIds })}
  onExplore={() => c.go({ view: "preview", c: c.collectionUrl, i: c.itemUrl })}
 />;
}

export const Route = createFileRoute("/")({ component: Discover });
