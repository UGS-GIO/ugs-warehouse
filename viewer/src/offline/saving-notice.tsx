// On-screen "keep this page open" while something is saving for offline. The browser's leave-page
// prompt (in-flight.ts) is generic and unreliable on iOS; this says it plainly on every device.
import { useSyncExternalStore } from "react";
import { savingCount, subscribe } from "./in-flight";

export function SavingNotice() {
  const n = useSyncExternalStore(subscribe, savingCount, () => 0);
  if (!n) return null;
  return (
    <div role="status"
      className="fixed inset-x-0 bottom-3 z-50 mx-auto w-fit max-w-[calc(100%-2rem)] rounded-md bg-card px-3 py-2 text-sm shadow-lg ring-1 ring-border">
      Saving {n > 1 ? `${n} files` : "a file"} for offline use. Keep this page open, or the save is cancelled.
    </div>
  );
}
