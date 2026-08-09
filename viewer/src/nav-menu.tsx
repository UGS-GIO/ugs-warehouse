/**
 * App menu (hamburger). Holds the theme picker, plus the views at widths where the tab row can't
 * fit. Base UI Menu, same as the soil-water app: opening moves focus into the menu, arrow keys and
 * typeahead navigate it, Escape closes and returns focus to the trigger — none of which the
 * hand-rolled popover this replaced could do. Theme is a radio group writing straight to theme.ts.
 */
import { Menu } from "@base-ui/react/menu";
import { useState } from "react";

import { getTheme, setTheme, type Theme } from "./theme";

const THEMES: { value: Theme; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "☀" },
  { value: "dark", label: "Dark", icon: "☾" },
  { value: "system", label: "System", icon: "◐" },
];

export type NavPage = { id: string; label: string; onSelect: () => void };

const ITEM = "flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[13px] outline-none data-[highlighted]:bg-muted";
const HEADING = "px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground";

export function NavMenu({ pages, current }: { pages: NavPage[]; current: string }) {
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const pick = (value: Theme) => {
    setTheme(value);
    setThemeState(value);
  };

  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label="Menu"
        className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card text-foreground hover:bg-muted"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Menu.Popup className="min-w-44 rounded-md border border-border bg-card p-1 text-foreground shadow-lg outline-none">
            {/* Views only where the tab row is hidden; the theme picker is always here. */}
            {pages.length > 0 && (
              <div className="md:hidden">
                <div className={HEADING}>Views</div>
                {pages.map((p) => (
                  <Menu.Item key={p.id} onClick={p.onSelect}
                    className={`${ITEM} ${p.id === current ? "text-primary" : ""}`}>
                    {p.label}
                  </Menu.Item>
                ))}
                <Menu.Separator className="my-1 h-px bg-border" />
              </div>
            )}
            <div className={HEADING}>Theme</div>
            <Menu.RadioGroup value={theme} onValueChange={(value) => pick(value as Theme)}>
              {THEMES.map((t) => (
                <Menu.RadioItem key={t.value} value={t.value} className={ITEM}>
                  <span aria-hidden className="w-4 text-center">{t.icon}</span>
                  <span className="flex-1">{t.label}</span>
                  <Menu.RadioItemIndicator className="text-primary">✓</Menu.RadioItemIndicator>
                </Menu.RadioItem>
              ))}
            </Menu.RadioGroup>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
