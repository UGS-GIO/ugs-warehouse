// Shared class names and small text helpers.
import { CARD_GRID } from "./page";

export const C = {
  wrap: "w-full px-4 py-6 sm:px-6 lg:px-10",
  crumb: "text-primary cursor-pointer",
  muted: "text-xs text-muted-foreground",
  grid: `mt-3.5 ${CARD_GRID}`,
  card: "flex flex-col rounded-lg border border-border bg-card px-4 py-3.5 cursor-pointer hover:border-primary hover:shadow-sm transition",
  cardTitle: "mb-1.5 text-sm font-semibold leading-tight",
  badge: "mr-1.5 mt-1 inline-block rounded border border-border bg-muted px-1.5 py-px text-xs text-muted-foreground",
  chip: "mr-1.5 mt-1.5 inline-block rounded bg-primary px-2 py-0.5 text-xs text-primary-foreground no-underline hover:opacity-90",
  input: "w-full sm:w-72 rounded-md border border-input bg-card px-2.5 py-1.5 text-sm text-foreground placeholder:text-muted-foreground",
  bar: "my-2 flex flex-wrap items-center gap-2.5",
  th: "cursor-pointer whitespace-nowrap border-b border-border px-2.5 py-1.5 text-left text-xs uppercase tracking-wide text-muted-foreground",
  thPlain: "whitespace-nowrap border-b border-border px-2.5 py-1.5 text-left text-xs uppercase tracking-wide text-muted-foreground",
  td: "border-b border-border px-2.5 py-1.5 align-top text-sm",
};

export const toggle = (on: boolean) =>
  `cursor-pointer border border-border px-2.5 py-1 text-xs text-foreground ${on ? "bg-accent" : "bg-card"}`;

export const humanize = (id: string) =>
  id.replace(/^ugs-/, "").replace(/[-_]+/g, " ").replace(/\b\w/g, (ch) => ch.toUpperCase());
