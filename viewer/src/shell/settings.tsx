// Settings: the preferences this device keeps, in one place. Each one is stored where it always
// was (theme.ts, data-saver.ts, recent-searches.ts, the offline store); this page only shows them.
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { type DataSaverPref, setPref, useDataSaver, useDataSaverPref } from "@/lib/data-saver";
import { formatBytes, isSupported } from "@/offline/opfs";
import { useOffline } from "@/offline/store";
import { UiSegmented } from "@/ui/segmented";

import { clearRecent, useRecent } from "./recent-searches";
import { setTheme, type Theme, useThemePref } from "./theme";

const THEMES: { value: Theme; label: string }[] = [
  { value: "light", label: "Light" }, { value: "dark", label: "Dark" }, { value: "system", label: "System" },
];
const SAVER: { value: DataSaverPref; label: string }[] = [
  { value: "auto", label: "Auto" }, { value: "on", label: "On" }, { value: "off", label: "Off" },
];

function Setting({ title, help, children }: { title: string; help: ReactNode; children: ReactNode }) {
  return (
    <section aria-label={title} className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2 border-b border-border py-4 last:border-0">
      <div className="max-w-md">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{help}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </section>
  );
}

export function Settings() {
  const theme = useThemePref();
  const saver = useDataSaverPref();
  const saverActive = useDataSaver();
  const recent = useRecent();
  const offline = useOffline();
  const used = offline.files.reduce((t, f) => t + f.bytes, 0) + offline.engineBytes;
  return (
    <div className="mx-auto w-full max-w-3xl px-4 py-6">
      <h1 className="text-xl font-semibold text-foreground">Settings</h1>
      <p className="mt-1 text-sm text-muted-foreground">Kept on this device only.</p>
      <div className="mt-4">
        <Setting title="Theme" help="System follows your device.">
          <UiSegmented value={theme} onValueChange={setTheme} items={THEMES} />
        </Setting>
        <Setting title="Data saver"
          help={<>Holds back map previews until you ask for them. Auto turns it on for slow or metered
            connections where the browser reports one. {saver === "auto" && `Now ${saverActive ? "on" : "off"}.`}</>}>
          <UiSegmented value={saver} onValueChange={setPref} items={SAVER} />
        </Setting>
        {isSupported() && (
          <Setting title="Offline data"
            help={offline.files.length
              ? `${offline.files.length} saved ${offline.files.length === 1 ? "file" : "files"}, ${formatBytes(used)} in all.`
              : "Nothing saved yet. Save a layer from its page or from the Map."}>
            <Link to="/offline" className="text-sm font-medium text-primary hover:underline">Manage offline data</Link>
          </Setting>
        )}
        <Setting title="Recent searches"
          help={recent.length ? `${recent.length} kept for the search box's suggestions.` : "None kept."}>
          <button type="button" onClick={clearRecent} disabled={!recent.length}
            className="rounded border border-border px-3 py-1.5 text-sm text-foreground hover:border-primary disabled:opacity-50">
            Clear
          </button>
        </Setting>
      </div>
    </div>
  );
}
