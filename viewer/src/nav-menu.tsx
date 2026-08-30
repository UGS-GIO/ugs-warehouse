/**
 * App menu (hamburger). Holds the theme picker, plus the views at widths where the tab row can't
 * fit. Base UI Menu, same as the soil-water app: opening moves focus into the menu, arrow keys and
 * typeahead navigate it, Escape closes and returns focus to the trigger — none of which the
 * hand-rolled popover this replaced could do. Theme is a radio group writing straight to theme.ts.
 */
import { Menu } from "@base-ui/react/menu";
import { useState } from "react";

import { CheckIcon, CopyIcon, copyCatalogUrl } from "./stac-url-chip";
import { getTheme, setTheme, type Theme, useIsDark } from "./theme";

const THEMES: { value: Theme; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "☀" },
  { value: "dark", label: "Dark", icon: "☾" },
  { value: "system", label: "System", icon: "◐" },
];

export type NavPage = { id: string; label: string; onSelect: () => void };

const ITEM = "flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted";
const HEADING = "px-2 py-1 text-sm font-semibold uppercase tracking-wider text-muted-foreground";

export function NavMenu({ pages, overflow = [], current, catalogUrl }: {
  pages: NavPage[];      // the primary tabs — shown here only below md, where the tab row is hidden
  overflow?: NavPage[];  // secondary views (Architecture/Guide/Developers/Review) — always in the menu
  current: string; catalogUrl?: string;
}) {
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const dark = useIsDark();
  const pick = (value: Theme) => {
    setTheme(value);
    setThemeState(value);
  };

  const copy = async () => {
    setCopied(catalogUrl && (await copyCatalogUrl(catalogUrl)) ? "copied" : "failed");
  };

  return (
    // Closing resets the copy state — the menu's own lifetime is the feedback's, so no timer.
    <Menu.Root onOpenChange={(open) => !open && setCopied("idle")}>
      {/* One menu, two faces: a hamburger below md (it carries the views too), and the current
          theme's own icon on desktop, where the views are already tabs. */}
      <Menu.Trigger
        aria-label={pages.length ? "Menu" : "Theme"}
        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:text-foreground md:h-8 md:w-8"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden className="md:hidden">
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden className="hidden md:block">
          {dark
            ? <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z" />
            : <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" /></>}
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
            {/* Secondary views live only here (both desktop + mobile) so the tab row never overflows. */}
            {overflow.length > 0 && (
              <div>
                <div className={HEADING}>More</div>
                {overflow.map((p) => (
                  <Menu.Item key={p.id} onClick={p.onSelect}
                    className={`${ITEM} ${p.id === current ? "text-primary" : ""}`}>
                    {p.label}
                  </Menu.Item>
                ))}
                <Menu.Separator className="my-1 h-px bg-border" />
              </div>
            )}
            {/* The header chip is desktop-only, so below md this is the one way to get the URL. */}
            {catalogUrl && (
              <div className="md:hidden">
                {copied === "failed" ? (
                  // No clipboard (denied, or a non-secure origin) — open it instead of dead-ending.
                  <Menu.Item className={ITEM} nativeButton={false}
                    render={<a href={catalogUrl} target="_blank" rel="noreferrer" />}>
                    <CopyIcon />
                    <span className="flex-1">Open STAC catalog</span>
                  </Menu.Item>
                ) : (
                  <Menu.Item className={ITEM} closeOnClick={false} onClick={copy}>
                    {copied === "copied" ? <CheckIcon /> : <CopyIcon />}
                    <span className="flex-1">{copied === "copied" ? "Copied" : "Copy STAC URL"}</span>
                  </Menu.Item>
                )}
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
