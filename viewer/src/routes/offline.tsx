import { createFileRoute } from "@tanstack/react-router";

import { OfflineManager } from "@/offline/offline-manager";

export const Route = createFileRoute("/offline")({ component: OfflineManager });
