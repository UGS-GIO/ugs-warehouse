// Light / dark / system theme. Toggles the `.dark` class on <html>, which the Tailwind token
// palette (index.css) keys off. Persisted to localStorage; on "system" it re-applies on OS change.
// Applied once in main.tsx before render so there's no flash. The official Utah header stays light
// by design (state identity) — only the app body themes.
import { useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";

const KEY = "vite-ui-theme";   // unchanged, so an existing light/dark choice carries over
const mq = () => window.matchMedia("(prefers-color-scheme: dark)");

export function getTheme(): Theme {
  const t = localStorage.getItem(KEY);
  return t === "light" || t === "dark" ? t : "system";
}

const isDark = (theme: Theme): boolean => theme === "dark" || (theme === "system" && mq().matches);

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", isDark(theme));
  subscribers.forEach((notify) => notify());
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(KEY, theme);
  applyTheme(theme);
}

// Follow OS changes while on "system" — a module-level listener, no React effect.
mq().addEventListener("change", () => {
  if (getTheme() === "system") applyTheme("system");
});

// The RESOLVED appearance, for the few things that must render one way or the other (mermaid's
// theme). A store, so consumers subscribe instead of re-deriving it in an effect.
const subscribers = new Set<() => void>();
const subscribe = (notify: () => void) => {
  subscribers.add(notify);
  return () => { subscribers.delete(notify); };
};
export const useIsDark = (): boolean =>
  useSyncExternalStore(subscribe, () => document.documentElement.classList.contains("dark"));
