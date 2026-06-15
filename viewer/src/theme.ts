// Light/dark theme, mirroring ugs-map-viewer: `.dark` class on <html>, persisted in
// localStorage under `vite-ui-theme`, default dark.
import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const KEY = "vite-ui-theme";

export const initialTheme = (): Theme =>
  (localStorage.getItem(KEY) as Theme | null) ?? "dark";

/** Apply before first paint (call in main.tsx) so there's no flash. */
export function applyTheme(t: Theme): void {
  document.documentElement.classList.toggle("dark", t === "dark");
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem(KEY, theme);
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}
