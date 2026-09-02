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

afterEach(async () => {
  if (typeof document === "undefined") return;
  const { cleanup } = await import("@testing-library/react");
  cleanup();
});
