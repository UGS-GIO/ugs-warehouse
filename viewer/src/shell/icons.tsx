// Shared inline icons, until an icon library replaces them. Inline SVG so they take the text color.

const ICON = "h-3.5 w-3.5 shrink-0 stroke-current";

export function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={ICON} aria-hidden>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" className={ICON} aria-hidden>
      <path d="m4 13 5 5L20 6" />
    </svg>
  );
}

// Remove a saved copy.
export function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={ICON} aria-hidden>
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
    </svg>
  );
}

// Save-for-offline. An SVG rather than a glyph: "⭳" is missing from common phone fonts and
// rendered as a crossed box.
export function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={ICON} aria-hidden>
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M5 21h14" />
    </svg>
  );
}

// GitHub's mark (Octicons mark-github).
const GITHUB_MARK = "M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z";

export function GitHubIcon({ size = 16 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden><path d={GITHUB_MARK} /></svg>;
}
