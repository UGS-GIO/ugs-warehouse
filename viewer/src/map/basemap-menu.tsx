import { Menu } from "@base-ui/react/menu";

const ITEM = "flex cursor-pointer select-none items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-muted";

/** The basemap picker as one square button, so it does not take a row of the map. */
export function BasemapMenu<T extends string>({ value, onValueChange, items }: {
  value: T;
  onValueChange: (v: T) => void;
  items: readonly T[];
}) {
  return (
    <Menu.Root>
      <Menu.Trigger aria-label={`Basemap: ${value}`} title="Basemap"
        className="flex size-9 items-center justify-center rounded-md border border-input bg-card/95 text-foreground shadow hover:bg-hover data-[popup-open]:bg-muted">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" />
        </svg>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Menu.Popup className="min-w-36 rounded-md border border-border bg-card p-1 text-foreground shadow-lg outline-none">
            <div className="px-2 pb-1 pt-1.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Basemap</div>
            <Menu.RadioGroup value={value} onValueChange={(v) => onValueChange(v as T)}>
              {items.map((b) => (
                <Menu.RadioItem key={b} value={b} className={ITEM} closeOnClick>
                  <span className="flex-1">{b}</span>
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
