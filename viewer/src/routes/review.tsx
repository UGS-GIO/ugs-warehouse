import { createFileRoute } from "@tanstack/react-router";

import { useViewCtx } from "@/app";
import { ReviewDashboard } from "@/review/review-dashboard";

// Review data are vector serving-topics → the ugs-serving-topics collection. Open the item there.
function Review() {
  const { go } = useViewCtx();
  return <ReviewDashboard onOpen={(itemId) => go({ view: "catalog", c: "ugs-serving-topics", i: itemId })} />;
}

export const Route = createFileRoute("/review")({ component: Review });
