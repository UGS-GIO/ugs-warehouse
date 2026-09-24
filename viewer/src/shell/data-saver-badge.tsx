// Shows that data saver is holding things back, and turns it off in one tap. Off is a setting of
// its own, so a slow connection does not turn it straight back on (the menu offers Auto again).
import { setPref, useDataSaver } from "@/lib/data-saver";

export function DataSaverBadge() {
  if (!useDataSaver()) return null;
  return (
    <button type="button" onClick={() => setPref("off")}
      title="Data saver is on: maps, previews and pictures load only when you ask. Tap to turn it off."
      className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground pointer-coarse:min-h-11">
      Data saver
    </button>
  );
}
