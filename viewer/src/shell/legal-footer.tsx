// The state's required links as one line. Replaces the Design System's own footer, which is 269px
// of stacked links — most of a phone's map (index.css hides it). Desktop only: on a phone the same
// links live in the app menu (nav-menu.tsx), so no screen space goes to them.
export const LEGAL_LINKS = [
  { href: "https://www.utah.gov/index.html", label: "Utah.gov Home" },
  { href: "https://www.utah.gov/support/disclaimer.html", label: "Terms of Use" },
  { href: "https://www.utah.gov/support/privacypolicy.html", label: "Privacy Policy" },
  { href: "https://dts.utah.gov/accessibility", label: "Accessibility" },
];

export const REPO_URL = "https://github.com/UGS-GIO/ugs-warehouse";

// GitHub's mark (Octicons mark-github, 16x16 viewBox).
export const GITHUB_MARK = "M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z";

export const BUILD_URL = __BUILD_SHA__ ? `${REPO_URL}/tree/${__BUILD_SHA__}` : REPO_URL;

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
      <a href={BUILD_URL} target="_blank" rel="noreferrer" title={`viewer build ${__BUILD_HASH__} on GitHub`}
         className="inline-flex items-center gap-1 hover:text-foreground hover:underline">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden><path d={GITHUB_MARK} /></svg>
        build {__BUILD_DATE__} · {__BUILD_HASH__}
      </a>
      <a href="https://geology.utah.gov" target="_blank" rel="noreferrer" className="ml-auto hover:text-foreground hover:underline">
        Utah Geological Survey
      </a>
    </footer>
  );
}
