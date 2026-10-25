/**
 * The one disclosure chevron, for every accordion: points right closed, down open. With `open`
 * unset it sits in a <summary> and turns with its <details>.
 */
export function Chevron({ open, className = "" }: { open?: boolean; className?: string }) {
  const turn = open === undefined ? "[details[open]>summary_&]:rotate-90" : open ? "rotate-90" : "";
  return (
    <svg aria-hidden viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round"
      className={`size-[18px] shrink-0 transition-transform ${turn} ${className}`}>
      <path d="M6 3.5 10.5 8 6 12.5" />
    </svg>
  );
}
