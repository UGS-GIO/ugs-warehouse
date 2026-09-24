// Post-build guard on the generated service worker. Runs as part of `npm run build`, so a bad
// precache fails the build instead of the field.
//
// The failure it exists for: a `**/duckdb-*` globIgnore also swallowed our own 1 KiB duckdb.ts
// wrapper chunk, which /discover statically imports. The build was clean, the SW was clean, and
// the route white-screened offline with "Failed to fetch dynamically imported module". Only an
// assertion over the built manifest catches that.
import { readdirSync, readFileSync, existsSync } from "node:fs";

const DIST = new URL("../dist/", import.meta.url);
const at = (p) => new URL(p, DIST);

// Deliberately excluded (vite.config.ts globIgnores): the `mvp` engine worker, for browsers that
// offline tables do not serve. The loader and `eh` worker are precached for offline tables.
const EXPECTED_ABSENT = /^duckdb-browser-mvp/;

const fail = (msg) => {
  console.error(`✗ precache check: ${msg}`);
  process.exit(1);
};

if (!existsSync(at("sw.js"))) fail("dist/sw.js missing — did VitePWA run?");

const sw = readFileSync(at("sw.js"), "utf8");
// The key is quoted under injectManifest and bare under generateSW; accept either so a change of
// authoring strategy cannot quietly turn this check into a no-op.
const precached = new Set([...sw.matchAll(/"?url"?:"([^"]+)"/g)].map((m) => m[1]));

const js = readdirSync(at("assets")).filter((f) => f.endsWith(".js"));
// Listed, not all installed: sw.ts installs the shell and fetches the rest on a save for offline,
// both from this list.
const missing = js.filter((f) => !EXPECTED_ABSENT.test(f) && !precached.has(`assets/${f}`));
if (missing.length) {
  fail(`${missing.length} app chunk(s) not in the manifest, so their routes break offline:\n  ${missing.join("\n  ")}`);
}
if (![...precached].some((u) => /^assets\/index-[^/]+\.js$/.test(u))) fail("no assets/index-*.js entry chunk: sw-routes isShell installs nothing to boot from");

const present = js.filter((f) => EXPECTED_ABSENT.test(f) && precached.has(`assets/${f}`));
if (present.length) fail(`mvp engine chunks precached, though offline tables never use them: ${present.join(", ")}`);

// Assert against the worker SOURCE, not the bundle: class names are minified away, so matching
// the built file would only ever pass by accident.
const swSrc = readFileSync(new URL("../src/sw.ts", import.meta.url), "utf8");
if (!swSrc.includes("NavigationRoute")) fail("sw.ts registers no NavigationRoute — SPA deep links will 404 offline");

const manifest = JSON.parse(readFileSync(at("manifest.webmanifest"), "utf8"));
if (!manifest.icons?.some((i) => i.sizes === "512x512")) fail("manifest has no 512px icon — not installable");

const app = js.filter((f) => !EXPECTED_ABSENT.test(f)).length;
console.log(`✓ precache check: ${precached.size} entries, ${app} app chunks, manifest ok`);
