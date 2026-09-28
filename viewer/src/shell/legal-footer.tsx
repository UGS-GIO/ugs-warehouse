// The state's required links as one line. Replaces the Design System's own footer, which is 269px
// of stacked links — most of a phone's map (index.css hides it). Desktop only: on a phone the same
// links live in the app menu (nav-menu.tsx), so no screen space goes to them.
export const LEGAL_LINKS = [
  { href: "https://www.utah.gov/index.html", label: "Utah.gov Home" },
  { href: "https://www.utah.gov/support/disclaimer.html", label: "Terms of Use" },
  { href: "https://www.utah.gov/support/privacypolicy.html", label: "Privacy Policy" },
  { href: "https://dts.utah.gov/accessibility", label: "Accessibility" },
];

export function LegalFooter({ className = "", catalogUrl }: { className?: string; catalogUrl?: string }) {
  return (
    <footer className={`hidden flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-3 py-1.5 text-xs text-muted-foreground md:flex ${className}`}>
      <span>An official website of the state of Utah · © state of Utah</span>
      {LEGAL_LINKS.map((l) => (
        <a key={l.label} href={l.href} target="_blank" rel="noreferrer" className="hover:text-foreground hover:underline">
          {l.label}
        </a>
      ))}
      {catalogUrl && (
        <a href={catalogUrl} target="_blank" rel="noreferrer" title={catalogUrl}
           className="hover:text-foreground hover:underline">
          STAC catalog
        </a>
      )}
      {/* Hash inline, not just in the tooltip: on a per-PR preview it's how you tell which bundle
          you're actually looking at. */}
      <span title={`viewer build ${__BUILD_HASH__}`}>build {__BUILD_DATE__} · {__BUILD_HASH__}</span>
      <a href="https://geology.utah.gov" target="_blank" rel="noreferrer" className="ml-auto hover:text-foreground hover:underline">
        Utah Geological Survey
      </a>
    </footer>
  );
}
