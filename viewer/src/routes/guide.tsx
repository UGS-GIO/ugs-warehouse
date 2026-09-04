import { createFileRoute } from "@tanstack/react-router";

import { Guide } from "../shell/guide";

export const Route = createFileRoute("/guide")({ component: Guide });
