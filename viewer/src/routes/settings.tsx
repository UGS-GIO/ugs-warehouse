import { createFileRoute } from "@tanstack/react-router";

import { Settings } from "@/shell/settings";

export const Route = createFileRoute("/settings")({ component: Settings });
