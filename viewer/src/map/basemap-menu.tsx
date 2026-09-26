import { Menu } from "@base-ui/react/menu";

const ITEM = "flex cursor-pointer select-none items-center gap-2 rounded px-1.5 py-1.5 text-sm outline-none data-[highlighted]:bg-muted";

/** The basemap picker as one map control button (maplibre's own control look, like geolocate), so it
 *  does not take a row of the map. `thumbs` are small captures of each basemap over Salt Lake City. */
export function BasemapMenu<T extends string>({ value, onValueChange, items, thumbs }: {
  value: T;
  onValueChange: (v: T) => void;
  items: readonly T[];
  thumbs: Record<T, string>;
}) {
  return (
    <Menu.Root>
      {/* The MapControl wrapper already spaces it; maplibre's own .maplibregl-ctrl margin would double that. */}
      <div className="maplibregl-ctrl maplibregl-ctrl-group" style={{ margin: 0 }}>
        <Menu.Trigger aria-label={`Basemap: ${value}`} title="Basemap"
          className="flex items-center justify-center text-foreground data-[popup-open]:bg-muted">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
            strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" />
          </svg>
        </Menu.Trigger>
      </div>
      <Menu.Portal>
        <Menu.Positioner side="bottom" align="end" sideOffset={6} className="z-50">
          <Menu.Popup className="min-w-44 rounded-md border border-border bg-card p-1 text-foreground shadow-lg outline-none">
            <div className="px-2 pb-1 pt-1.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Basemap</div>
            <Menu.RadioGroup value={value} onValueChange={(v) => onValueChange(v as T)}>
              {items.map((b) => (
                <Menu.RadioItem key={b} value={b} className={ITEM} closeOnClick>
                  <img src={thumbs[b]} alt="" width={48} height={32}
                    className="h-8 w-12 shrink-0 rounded-sm border border-border object-cover" />
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
