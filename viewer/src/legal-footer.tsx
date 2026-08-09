// The state's required links as one line. Replaces the Design System's own footer, which is 269px
// of stacked links — most of a phone's map (index.css hides it).
const LINKS = [
  { href: "https://www.utah.gov", label: "Utah.gov" },
  { href: "https://www.utah.gov/support/disclaimer.html", label: "Terms" },
  { href: "https://www.utah.gov/support/privacypolicy.html", label: "Privacy" },
  { href: "https://dts.utah.gov/accessibility", label: "Accessibility" },
];

export function LegalFooter({ className = "" }: { className?: string }) {
  return (
    <footer className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground ${className}`}>
      <span>© State of Utah</span>
      {LINKS.map((l) => (
        <a key={l.label} href={l.href} target="_blank" rel="noreferrer" className="hover:text-foreground hover:underline">
          {l.label}
        </a>
      ))}
      <a href="https://geology.utah.gov" target="_blank" rel="noreferrer" className="ml-auto hover:text-foreground hover:underline">
        Utah Geological Survey
      </a>
    </footer>
  );
}
