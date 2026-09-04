import { createFileRoute } from "@tanstack/react-router";

import { useViewCtx } from "@/app";
import { Landing } from "@/discover/landing";

// The param-less front door. Reuses the same loaded item set as Map/Discover (mapItems) — no extra
// fetch — for its category-tile counts + "recently updated" strip. Everything hands off to Discover.
function LandingPage() {
const { mapItems, mapLoadKey, mapItemsLoading, openDiscoverSearch } = useViewCtx();
return <Landing
  items={mapItems}
  itemsKey={mapLoadKey}
  loading={mapItemsLoading}
  onSearch={(text: string) => openDiscoverSearch({ q: text })}
  onOpenCategory={(key: string) => openDiscoverSearch({ category: key })}
 />;
}

export const Route = createFileRoute("/")({ component: LandingPage });
