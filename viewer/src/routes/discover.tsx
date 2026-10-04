import { createFileRoute } from "@tanstack/react-router";

import { idOf, useViewCtx } from "@/app";
import { DiscoveryView } from "@/discover/discovery-view";

// Full-width discovery split — same loaded item set as the map, its own facet rail / cards / synced
// map. Opening a card sets ?i= (staying here); the layout resolves the full doc → the drawer.
function Discover() {
const c = useViewCtx();
return <DiscoveryView
  items={c.mapItems}
  itemsKey={c.mapLoadKey}
  onOpenItem={c.openItem}
  onOpenPub={(collId, itemId) => c.go({ view: "catalog", c: collId, i: itemId })}
  itemSelected={Boolean(c.itemUrl)}
  selectedItem={c.item.data}
  selectedItemError={c.item.error}
  selectedCollectionId={c.collectionId}
  onCloseItem={() => c.go({ view: "discover" })}
  onViewOnMap={() => c.go({ view: "map", c: c.collectionUrl, i: c.itemUrl, l: c.itemUrl ? [idOf(c.itemUrl)] : c.layerIds })}
  onExplore={() => c.go({ view: "preview", c: c.collectionUrl, i: c.itemUrl })}
 />;
}

export const Route = createFileRoute("/discover")({ component: Discover });
