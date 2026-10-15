// Pricing v2 guard: every price the site shows must equal what checkout charges.
// Checkout charges plan.price_cents (backend/routers/payments.py). The seeded values live in
// backend/routers/plans.py DEFAULT_PLANS; this test reads them from there, checks the shared
// formatter shows them exactly, and scans the frontend for any other way of printing a price.
const fs = require("fs");
const path = require("path");
const { formatPlanPrice } = require("./lib/pricing");
const { PLAN_COPY, localizePlan } = require("./config/planCopy");

const root = path.resolve(__dirname, "..", "..");
const plansPy = fs.readFileSync(path.join(root, "backend/routers/plans.py"), "utf8");
const seeded = {};
for (const m of plansPy.matchAll(/"key": "([a-z_]+)", "name": "[^"]+", "price_cents": (\d+)/g)) seeded[m[1]] = Number(m[2]);

test("seeded checkout prices are the confirmed amounts", () => {
  expect(seeded).toEqual({ trial: 0, starter: 1999, growth: 4999, ai_office: 9999, high_volume: 19999, enterprise: 0 });
});

test("the shared formatter shows exactly the checkout amount", () => {
  const shown = Object.fromEntries(Object.entries(seeded).filter(([, c]) => c > 0).map(([k, c]) => [k, formatPlanPrice(c)]));
  expect(shown).toEqual({ starter: "$19.99", growth: "$49.99", ai_office: "$99.99", high_volume: "$199.99" });
  for (const c of Object.values(seeded)) expect(Number(formatPlanPrice(c).slice(1)) * 100).toBeCloseTo(c, 6);
});

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(jsx?|json)$/.test(e.name) && !/\.test\.js$/.test(e.name)) out.push(p);
  }
  return out;
}
const src = walk(path.join(__dirname));

test("no page formats price_cents itself (only lib/pricing.js may)", () => {
  const bad = src.filter((f) => !f.endsWith(path.join("lib", "pricing.js")))
    .filter((f) => /price_cents\s*\/\s*100/.test(fs.readFileSync(f, "utf8")))
    .map((f) => path.relative(__dirname, f));
  // admin/Plans.jsx is the admin editor (shows the raw stored value with cents) – allowed.
  expect(bad.filter((f) => !f.endsWith(path.join("admin", "Plans.jsx")))).toEqual([]);
});

test("no hard-coded plan price in copy or code", () => {
  const prices = /\$\s?(19|20|49|50|99|100|199|200)(\.\d\d)?(?!\d)/;
  const hits = src.filter((f) => !f.endsWith(path.join("lib", "pricing.js"))).filter((f) => prices.test(fs.readFileSync(f, "utf8"))).map((f) => path.relative(__dirname, f));
  expect(hits).toEqual([]);
});

test("every seeded plan and feature has Spanish copy", () => {
  const es = PLAN_COPY.es;
  for (const k of Object.keys(seeded)) expect(es.names[k]).toBeTruthy();
  const features = [...plansPy.matchAll(/"features": \[([^\]]*)\]/g)].flatMap((m) => [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]));
  expect(features.filter((f) => !es.features[f])).toEqual([]);
  const p = localizePlan({ key: "starter", name: "Starter", price_cents: 1999, description: "x", features: ["Basic SMS"] }, "es");
  expect(p).toMatchObject({ name: "Inicial", price_cents: 1999, features: ["SMS básicos"] });
  expect(localizePlan({ key: "starter", name: "Starter", features: ["Admin-added"] }, "es").features).toEqual(["Admin-added"]);
});
