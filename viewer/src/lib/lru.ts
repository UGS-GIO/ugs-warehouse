// Small LRU helpers shared by the caches that bound memory during navigation: the pmtiles archive
// cache (main.tsx) and the 3D fence/terrain mesh caches (Browse.tsx). JS Maps iterate in insertion
// order, so "oldest = first key" and re-inserting on use makes it most-recently-used.

/** Insert/refresh `key` as most-recently-used, evicting the oldest entries down to `cap`. */
export function lruSet<V>(cache: Map<string, V>, key: string, value: V, cap: number): void {
  cache.delete(key);       // re-insert → move to the newest (last) position
  cache.set(key, value);
  while (cache.size > cap) cache.delete(cache.keys().next().value as string);
}

/**
 * A Map that evicts its oldest entry whenever `set` pushes it past `cap`.
 *
 * `pinned` exempts keys from eviction. The pmtiles protocol needs it: an archive being served from
 * a downloaded file has no network copy to fall back to, so evicting it would silently turn an
 * offline layer into a failed fetch. A map of only pinned keys simply grows past the cap.
 */
export class CappedMap<K, V> extends Map<K, V> {
  constructor(private readonly cap: number, private readonly pinned?: (key: K) => boolean) { super(); }
  set(key: K, value: V): this {
    super.set(key, value);
    if (this.size <= this.cap) return this;
    for (const k of [...this.keys()]) {
      if (this.size <= this.cap) break;
      if (k !== key && !this.pinned?.(k)) super.delete(k);
    }
    return this;
  }
}
