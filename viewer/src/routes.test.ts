import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// The route tree is GENERATED from src/routes/ by @tanstack/router-plugin, so nothing in the app
// imports a route by name — delete a route file and the app still builds, just without that URL.
// These are the ten the nav, the docs and the Firebase rewrites (firebase.json) all assume exist.
// /search folded into Discover — one search box, articles as their own result group.
const EXPECTED = ["/", "/arch", "/catalog", "/developers", "/discover",
                  "/guide", "/map", "/preview", "/review"];

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

  // firebase.json rewrites each route to index.html. They are scoped deliberately (a catch-all made
  // a missing asset return 200 text/html, #213), so the list has to track the routes by hand — and
  // a route without one 404s on reload in production while working fine in dev.
  it("has a Firebase rewrite for every route", () => {
    const cfg = JSON.parse(
      readFileSync(fileURLToPath(new URL("../../firebase.json", import.meta.url)), "utf8"),
    ) as { hosting: { rewrites: { source: string }[] } };
    const rewritten = cfg.hosting.rewrites
      .map((r) => (r.source === "/" ? "/" : r.source.replace("{,/**}", "")))
      .sort();
    expect(rewritten).toEqual(EXPECTED);
  });
});
