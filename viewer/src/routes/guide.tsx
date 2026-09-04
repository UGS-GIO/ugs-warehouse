import { createFileRoute } from "@tanstack/react-router";

import { Guide } from "../guide";

export const Route = createFileRoute("/guide")({ component: Guide });
