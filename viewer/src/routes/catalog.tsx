import { createFileRoute } from "@tanstack/react-router";

import { Browse } from "../browse";

export const Route = createFileRoute("/catalog")({ component: Browse });
