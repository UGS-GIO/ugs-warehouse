// Small LRU helpers shared by the caches that bound memory during navigation: the pmtiles archive
// cache (main.tsx) and the 3D fence/terrain mesh caches (Browse.tsx). JS Maps iterate in insertion
// order, so "oldest = first key" and re-inserting on use makes it most-recently-used.

/** Insert/refresh `key` as most-recently-used, evicting the oldest entries down to `cap`. */
export function lruSet<V>(cache: Map<string, V>, key: string, value: V, cap: number): void {
  cache.delete(key);       // re-insert → move to the newest (last) position
  cache.set(key, value);
  while (cache.size > cap) cache.delete(cache.keys().next().value as string);
}

/** A Map that evicts its oldest entry whenever `set` pushes it past `cap`. */
export class CappedMap<K, V> extends Map<K, V> {
  constructor(private readonly cap: number) { super(); }
  set(key: K, value: V): this {
    super.set(key, value);
    while (this.size > this.cap) super.delete(this.keys().next().value as K);
    return this;
  }
}
