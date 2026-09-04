import { createFileRoute } from "@tanstack/react-router";

import { Guide } from "../app";

export const Route = createFileRoute("/guide")({ component: Guide });
