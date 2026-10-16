#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const en = JSON.parse(readFileSync(resolve(root, "frontend/src/i18n/locales/en.json"), "utf8"));
const es = JSON.parse(readFileSync(resolve(root, "frontend/src/i18n/locales/es.json"), "utf8"));

function leafKeys(obj, prefix = "") {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) out.push(...leafKeys(v, path));
    else out.push(path);
  }
  return out;
}

const enKeys = new Set(leafKeys(en));
const esKeys = new Set(leafKeys(es));
const missingInEs = [...enKeys].filter((k) => !esKeys.has(k)).sort();
const missingInEn = [...esKeys].filter((k) => !enKeys.has(k)).sort();

if (missingInEs.length || missingInEn.length) {
  console.error("i18n key parity FAILED");
  if (missingInEs.length) {
    console.error("Missing in es.json:");
    for (const k of missingInEs) console.error("  " + k);
  }
  if (missingInEn.length) {
    console.error("Missing in en.json:");
    for (const k of missingInEn) console.error("  " + k);
  }
  process.exit(1);
}
console.log(`i18n key parity OK (${enKeys.size} keys)`);
