// Runs for every test file. The DOM bits are guarded so the node-environment tests (the majority)
// pay nothing for them.
import { afterEach } from "vitest";

if (typeof window !== "undefined" && !window.matchMedia) {
  // jsdom has no matchMedia; the theme and breakpoint hooks subscribe to one.
  window.matchMedia = (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }) as MediaQueryList;
}

if (typeof window !== "undefined") {
  // Node 24+ exposes a built-in global `localStorage` (default-on since Node 25). Without
  // `--localstorage-file` it's a degenerate object with no getItem/setItem/clear, and vitest won't
  // let jsdom's Storage replace an existing Node global — so jsdom tests, and the hooks they
  // exercise that use `localStorage` directly, hit the broken one and throw "clear is not a
  // function". Install a working in-memory Storage when the ambient one is unusable. (Disabling
  // Node's Web Storage via a node flag would be cleaner, but vitest doesn't forward poolOptions
  // execArgv to its workers, so the flag never reaches the test process.)
  let usable = false;
  try {
    const ls = globalThis.localStorage;
    usable = typeof ls?.clear === "function" && typeof ls?.getItem === "function";
  } catch {
    usable = false;
  }
  if (!usable) {
    const store = new Map<string, string>();
    const storage: Storage = {
      get length() {
        return store.size;
      },
      clear() {
        store.clear();
      },
      getItem(key) {
        const k = String(key);
        return store.has(k) ? store.get(k)! : null;
      },
      key(index) {
        return Array.from(store.keys())[index] ?? null;
      },
      removeItem(key) {
        store.delete(String(key));
      },
      setItem(key, value) {
        store.set(String(key), String(value));
      },
    };
    Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
  }
}

afterEach(async () => {
  if (typeof document === "undefined") return;
  const { cleanup } = await import("@testing-library/react");
  cleanup();
});
