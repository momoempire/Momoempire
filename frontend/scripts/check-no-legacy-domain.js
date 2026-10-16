#!/usr/bin/env node
// Fails (exit 1) if the old, unowned domain "aioffice.io" appears in a built bundle.
// Contact address and site URL come from REACT_APP_CONTACT_EMAIL / REACT_APP_SITE_URL instead.
// Usage: node scripts/check-no-legacy-domain.js [buildDir=build]
const fs = require("fs");
const path = require("path");

const PATTERN = /aioffice\.io/i;

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

function findLegacyDomain(dir) {
  return walk(dir).filter((f) => PATTERN.test(fs.readFileSync(f, "latin1"))).map((f) => path.relative(dir, f));
}

module.exports = { findLegacyDomain, PATTERN };

if (require.main === module) {
  const dir = path.resolve(process.argv[2] || "build");
  if (!fs.existsSync(dir)) {
    console.error(`check-no-legacy-domain: ${dir} does not exist; build first.`);
    process.exit(2);
  }
  const hits = findLegacyDomain(dir);
  if (hits.length) {
    console.error(`check-no-legacy-domain: "aioffice.io" found in:\n  ${hits.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`check-no-legacy-domain: OK (${dir})`);
}
