import { createFileRoute } from "@tanstack/react-router";

import { Architecture } from "@/shell/architecture";

export const Route = createFileRoute("/arch")({ component: Architecture });
