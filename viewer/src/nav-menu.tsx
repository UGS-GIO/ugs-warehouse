// The view switcher as a hamburger, for widths where the tab row can't fit (it used to scroll
// sideways off the edge). Native popover: the platform gives light-dismiss, Escape, focus and
// top-layer stacking, so this needs no listeners, no open state and no menu library.
export type NavPage = { id: string; label: string; onSelect: () => void };

const MENU_ID = "view-menu";

export function NavMenu({ pages, current }: { pages: NavPage[]; current: string }) {
  return (
    <>
      <button
        type="button"
        aria-label="Views"
        popoverTarget={MENU_ID}
        className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-card text-foreground hover:bg-accent"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
          <path d="M3 6h18M3 12h18M3 18h18" />
        </svg>
      </button>
      {/* The UA centres a popover with `inset: 0; margin: auto`; pin it under the trigger instead. */}
      <div
        id={MENU_ID}
        popover="auto"
        role="menu"
        className="fixed inset-auto right-2 top-12 m-0 min-w-44 rounded-md border border-border bg-card p-1 text-foreground shadow-lg"
      >
        {pages.map((p) => (
          <button
            key={p.id}
            type="button"
            role="menuitem"
            aria-current={p.id === current ? "page" : undefined}
            popoverTarget={MENU_ID}
            popoverTargetAction="hide"
            onClick={p.onSelect}
            className={`flex w-full items-center rounded px-2 py-1.5 text-left text-[13px] hover:bg-accent ${p.id === current ? "text-primary" : "text-foreground"}`}
          >
            {p.label}
          </button>
        ))}
      </div>
    </>
  );
}
