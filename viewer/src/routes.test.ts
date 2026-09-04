import { readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// The route tree is GENERATED from src/routes/ by @tanstack/router-plugin, so nothing in the app
// imports a route by name — delete a route file and the app still builds, just without that URL.
// These are the ten the nav, the docs and the Firebase rewrites (firebase.json) all assume exist.
const EXPECTED = ["/", "/arch", "/catalog", "/developers", "/discover",
                  "/guide", "/map", "/preview", "/review", "/search"];

const routeDir = fileURLToPath(new URL("./routes", import.meta.url));

describe("the route tree", () => {
  it("has a file for every URL, named after it", () => {
    const paths = readdirSync(routeDir)
      .filter((f) => f.endsWith(".tsx") && f !== "__root.tsx")
      .map((f) => (f === "index.tsx" ? "/" : `/${f.replace(/\.tsx$/, "")}`))
      .sort();
    expect(paths).toEqual(EXPECTED);
  });

  it("keeps a root layout, which owns validateSearch", () => {
    expect(readdirSync(routeDir)).toContain("__root.tsx");
  });
});
