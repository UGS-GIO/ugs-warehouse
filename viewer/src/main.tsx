import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import React from "react";
import ReactDOM from "react-dom/client";
import { queryClient } from "./query-client";
import { router } from "./router";
import { applyTheme, getTheme } from "./shell/theme";
import "@fontsource-variable/source-sans-3";   // Utah DS body font, self-hosted
import "@utahdts/utah-design-system-header/css";
import "./index.css";

// Apply persisted theme before first paint to avoid a flash.
applyTheme(getTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
