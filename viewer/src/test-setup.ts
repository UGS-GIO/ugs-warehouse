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

if (typeof document !== "undefined") {
  // CI runs every test file at once on a few CPUs; 1s for a findBy is too short there.
  const { configure } = await import("@testing-library/react");
  configure({ asyncUtilTimeout: 5000 });
}

afterEach(async () => {
  if (typeof document === "undefined") return;
  const { cleanup } = await import("@testing-library/react");
  cleanup();
});
