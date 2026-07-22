// Pure catalog-tree helpers (no browser globals, so unit-testable in node).

// Root leaf collections = map layers (excluded from the by-date "All items" list); sub-catalogs = pubs.
export const layerCollectionIds = (rootChildren: { id: string; kind?: string }[]): string[] =>
  rootChildren.filter((c) => c.kind === "collection").map((c) => c.id);
