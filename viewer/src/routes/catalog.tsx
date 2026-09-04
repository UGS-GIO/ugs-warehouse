import { createFileRoute } from "@tanstack/react-router";

import { Browse } from "../catalog/browse";

export const Route = createFileRoute("/catalog")({ component: Browse });
