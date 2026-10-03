import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";
import { registerSW } from "virtual:pwa-register";
import * as downloads from "./offline/queue";
import * as offlineStore from "./offline/store";
import { queryClient } from "./query-client";
import { router } from "./router";
import { applyTheme, getTheme } from "./shell/theme";
import "@fontsource-variable/source-sans-3";   // Utah DS body font, self-hosted
import "@utahdts/utah-design-system-header/css";
import "./index.css";

// Service worker: app shell + catalog JSON offline. `immediate` activates a waiting SW
// instead of holding the update until every tab closes. No-op in dev (devOptions off).
registerSW({ immediate: true });

// A thumbnail or cover that fails to load (offline, or a missing object) would otherwise draw the
// browser's broken-image icon. Mark it once, here, rather than at every <img>: index.css hides
// marked images and the card's own background stands in. Error events don't bubble, hence capture.
window.addEventListener("error", (e) => {
  if (e.target instanceof HTMLImageElement) e.target.dataset.broken = "";
}, true);

// Read what is on the device once, then let downloads left unfinished last visit carry on. The
// store re-reads itself as each one settles (offline/store.ts).
void offlineStore.refresh();
void downloads.run();

// Apply persisted theme before first paint to avoid a flash.
applyTheme(getTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
