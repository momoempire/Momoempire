#!/usr/bin/env node
// Fails (exit 1) if the internal project name ("momo", any case) appears in a built bundle.
// Usage: node scripts/check-brand-leak.js [buildDir=build]
const fs = require("fs");
const path = require("path");

const PATTERN = /momo/i;
const EXT = new Set([".html", ".js", ".css", ".json", ".txt", ".xml", ".map", ".svg", ""]);

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (EXT.has(path.extname(e.name)) || e.name.startsWith("_")) out.push(p);
  }
  return out;
}

// A source map's "mappings" field is base64 VLQ, so it can contain "momo" by chance (seen on
// Oct 13: "...MomO..." in main.js.map), which failed the build at random. Check every other
// field of a map (file, sources, sourcesContent, names), where a real leak would show up.
function textToCheck(f) {
  const raw = fs.readFileSync(f, "utf8");
  if (path.extname(f) !== ".map") return raw;
  try {
    const { mappings, ...rest } = JSON.parse(raw); // eslint-disable-line no-unused-vars
    return JSON.stringify(rest);
  } catch {
    return raw;
  }
}

function findLeaks(dir) {
  return walk(dir).filter((f) => PATTERN.test(textToCheck(f))).map((f) => path.relative(dir, f));
}

module.exports = { findLeaks, PATTERN };

if (require.main === module) {
  const dir = path.resolve(process.argv[2] || "build");
  if (!fs.existsSync(dir)) {
    console.error(`check-brand-leak: ${dir} does not exist; build first.`);
    process.exit(2);
  }
  const leaks = findLeaks(dir);
  if (leaks.length) {
    console.error(`check-brand-leak: internal name found in:\n  ${leaks.join("\n  ")}`);
    process.exit(1);
  }
  console.log(`check-brand-leak: OK (${dir})`);
}
