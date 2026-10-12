// Post-build guard on package.json `overrides`. An override pins a transitive dependency until
// upstream fixes it; once upstream drops the package, npm silently stops applying the override and
// it lingers as dead config. Fail instead, so the upstream bump that frees us also removes it.
import { readFileSync } from "node:fs";

const read = (p) => JSON.parse(readFileSync(new URL(`../${p}`, import.meta.url), "utf8"));
const { overrides = {} } = read("package.json");
const installed = new Set(
  Object.keys(read("package-lock.json").packages).map((k) => k.replace(/^.*node_modules\//, "")),
);

// Every package an override names, nested ones included; "." means the parent itself, and a key
// may carry a version range ("foo@^1").
const names = (o) => Object.entries(o).flatMap(([k, v]) => [
  ...(k === "." ? [] : [k.replace(/(?<=.)@.*$/, "")]),
  ...(v && typeof v === "object" ? names(v) : []),
]);
const all = [...new Set(names(overrides))];
const stale = all.filter((name) => !installed.has(name));
if (stale.length) {
  console.error(`✗ overrides check: no longer in the dependency tree, delete from package.json:`);
  for (const name of stale) console.error(`  - ${name}`);
  process.exit(1);
}
console.log(`✓ overrides check: ${all.length} overridden packages still in the tree`);
