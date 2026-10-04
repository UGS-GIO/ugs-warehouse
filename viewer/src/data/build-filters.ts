import type { ColFilter, ColType } from "./download";

// Typed per-column inputs → SQL-ready filters: numeric → range, anything else → substring.
export function buildFilters(draft: Record<string, { min?: string; max?: string; text?: string }>,
                             types: Record<string, ColType> | undefined): ColFilter[] {
  const filters: ColFilter[] = [];
  for (const [col, d] of Object.entries(draft)) {
    const kind = types?.[col] ?? "text";
    if (kind === "number") {
      const min = d.min?.trim() ? Number(d.min) : undefined;
      const max = d.max?.trim() ? Number(d.max) : undefined;
      if (Number.isFinite(min) || Number.isFinite(max)) filters.push({ col, kind, min, max });
    } else if (d.text?.trim()) {
      filters.push({ col, kind: "text", contains: d.text });
    }
  }
  return filters;
}
