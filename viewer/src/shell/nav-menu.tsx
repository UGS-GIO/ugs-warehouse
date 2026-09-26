/**
 * App menu (hamburger). Holds the theme picker, plus the views at widths where the tab row can't
 * fit. Base UI Menu, same as the soil-water app: opening moves focus into the menu, arrow keys and
 * typeahead navigate it, Escape closes and returns focus to the trigger — none of which the
 * hand-rolled popover this replaced could do. Theme is a radio group writing straight to theme.ts.
 */
import { Menu } from "@base-ui/react/menu";
import { useState } from "react";

import { CheckIcon, CopyIcon, copyCatalogUrl } from "@/catalog/stac-url-chip";
import { type DataSaverPref, setPref, useDataSaverPref } from "@/lib/data-saver";
import { LEGAL_LINKS } from "./legal-footer";
import { getTheme, setTheme, type Theme } from "./theme";

const SAVER: { value: DataSaverPref; label: string }[] = [
  { value: "auto", label: "Auto (slow connections)" },
  { value: "on", label: "On" },
  { value: "off", label: "Off" },
];

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
  const saver = useDataSaverPref();
  const [theme, setThemeState] = useState<Theme>(getTheme);
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
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
      {/* One menu, two faces: a hamburger below md (it carries the views too), and a labeled
          "More" on desktop. A bare theme icon here hid Architecture/Guide/Developers behind
          something that read as a light-switch — the word is what makes them findable. */}
      <Menu.Trigger
        aria-label={pages.length ? "Menu" : "More"}
        className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:text-foreground md:h-8 md:w-auto md:gap-1 md:px-2"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden className="md:hidden">
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
        <span className="hidden text-sm md:inline">More</span>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden className="hidden md:block">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Menu.Popup className="max-h-[var(--available-height)] min-w-44 overflow-y-auto rounded-md border border-border bg-card p-1 text-foreground shadow-lg outline-none">
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
                {/* On desktop the trigger already says "More"; the heading only earns its keep
                    below md, where it separates these from the primary Views. */}
                <div className={`${HEADING} md:hidden`}>More</div>
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
            {/* Phones hide the footer (legal-footer.tsx); its links live here instead. */}
            <div className="md:hidden">
              <div className={HEADING}>About</div>
              {[{ href: "https://geology.utah.gov", label: "Utah Geological Survey" }, ...LEGAL_LINKS].map((l) => (
                <Menu.Item key={l.label} className={ITEM} nativeButton={false}
                  render={<a href={l.href} target="_blank" rel="noreferrer" />}>
                  {l.label}
                </Menu.Item>
              ))}
              <p className="px-2 py-1 text-xs text-muted-foreground" title={`viewer build ${__BUILD_HASH__}`}>
                build {__BUILD_DATE__} · {__BUILD_HASH__}
              </p>
              <Menu.Separator className="my-1 h-px bg-border" />
            </div>
            <div className={HEADING}>Theme</div>
            <Menu.RadioGroup value={theme} onValueChange={(value) => pick(value as Theme)}>
              {THEMES.map((t) => (
                // RadioItem keeps the menu open by default (for multi-pick groups); picking a
                // theme is one choice, and the result is visible behind the menu.
                <Menu.RadioItem key={t.value} value={t.value} className={ITEM} closeOnClick>
                  <span aria-hidden className="w-4 text-center">{t.icon}</span>
                  <span className="flex-1">{t.label}</span>
                  <Menu.RadioItemIndicator className="text-primary">✓</Menu.RadioItemIndicator>
                </Menu.RadioItem>
              ))}
            </Menu.RadioGroup>
            <Menu.Separator className="my-1 h-px bg-border" />
            {/* Holds back previews (the item map) on a slow or metered connection; see
                lib/data-saver.ts. Auto follows the browser where it says; iPhones don't. */}
            <div className={HEADING}>Data saver</div>
            <Menu.RadioGroup value={saver} onValueChange={(value) => {
              const o = SAVER.find((x) => x.value === value);
              if (o) setPref(o.value);
            }}>
              {SAVER.map((o) => (
                <Menu.RadioItem key={o.value} value={o.value} className={ITEM} closeOnClick>
                  <span className="flex-1">{o.label}</span>
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
