// Page-size choices + the range label for the catalog item lists. The paging itself is
// TanStack's (row model, clamping, auto-reset); this is only what it doesn't provide.

export const PAGE_SIZES = [25, 50, 100, 250] as const;
export const ALL_PAGES = "all" as const;
/** The page-size picker's choices. */
export const PAGE_SIZE_ITEMS = [...PAGE_SIZES.map((n) => ({ value: String(n), label: String(n) })), { value: ALL_PAGES, label: "All" }];

/** A page size: a row count, or every row on one page. */
export type PageSize = number | typeof ALL_PAGES;

export const DEFAULT_PAGE_SIZE: PageSize = 50;

/** "1–50 of 7545", or "0 of 0" when nothing matches. */
export function pageLabel(index: number, total: number, size: PageSize): string {
    if (total === 0) return "0 of 0";
    const per = size === ALL_PAGES ? total : size;
    const start = Math.min(index * per, Math.max(0, total - 1));
    return `${start + 1}–${Math.min(start + per, total)} of ${total}`;
}
