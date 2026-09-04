import { createFileRoute } from "@tanstack/react-router";

import { idOf, useViewCtx } from "../app";
import { PreviewView } from "../map/preview-view";

// The selected item full-screen, hosting the SAME Preview as the drawer inside the already-mounted
// preview map. Back returns to the Discover drawer it was launched from.
function Preview() {
const c = useViewCtx();
return <PreviewView
  item={c.item.data}
  loading={c.item.isLoading}
  onBack={() => c.go({ view: "discover", c: c.collectionUrl, i: c.itemUrl })}
  onMap={() => c.go({ view: "map", c: c.collectionUrl, i: c.itemUrl, l: c.itemUrl ? [idOf(c.itemUrl)] : c.layerIds })} />;
}

export const Route = createFileRoute("/preview")({ component: Preview });
