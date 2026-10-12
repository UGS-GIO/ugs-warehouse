// Post-build guard on package.json `overrides`. An override pins a transitive dependency until
// upstream fixes it; once upstream drops the package, npm silently stops applying the override and
// it lingers as dead config. Fail instead, so the upstream bump that frees us also removes it.
import { readFileSync } from "node:fs";

const read = (p) => JSON.parse(readFileSync(new URL(`../${p}`, import.meta.url), "utf8"));
const { overrides = {} } = read("package.json");
const installed = new Set(
  Object.keys(read("package-lock.json").packages).map((k) => k.replace(/^.*node_modules\//, "")),
);

const stale = Object.keys(overrides).filter((name) => !installed.has(name));
if (stale.length) {
  console.error(`✗ overrides check: no longer in the dependency tree, delete from package.json:`);
  for (const name of stale) console.error(`  - ${name}`);
  process.exit(1);
}
console.log(`✓ overrides check: ${Object.keys(overrides).length} overrides still apply`);
