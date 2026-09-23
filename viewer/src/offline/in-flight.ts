// Downloads in progress, and the one guard that protects them: leaving the page.
//
// Switching views inside the app does not cancel a download (the fetch outlives the component that
// started it); closing, reloading or navigating away does, and a half-written file is discarded.
// So while anything is saving, ask the browser to confirm before unloading. Browsers show their own
// generic wording and ignore custom text, and iOS Safari often skips the prompt entirely, which is
// why SavingNotice also says it on screen.

let active = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((fn) => fn());

/** Count `p` as an in-flight download until it settles. */
export function track<T>(p: Promise<T>): Promise<T> {
  active++;
  emit();
  return p.finally(() => { active--; emit(); });
}

export const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
export const savingCount = () => active;

if (typeof window !== "undefined") {
  window.addEventListener("beforeunload", (e) => {
    if (active === 0) return;
    e.preventDefault();
    e.returnValue = "";   // older Chromium needs this set to show the prompt at all
  });
}
