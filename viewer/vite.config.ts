import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// base: "./" so the built static bundle works under any CDN path.
export default defineConfig({ plugins: [react(), tailwindcss()], base: "./" });
