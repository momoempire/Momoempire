// The internal project name must never reach users. Public sources are always checked; if a
// build exists (e.g. after `REACT_APP_WAITLIST_ONLY=true yarn build`), the bundle is checked too.
const fs = require("fs");
const path = require("path");
const { findLeaks, PATTERN } = require("../scripts/check-brand-leak");
const { DEFAULT_BRAND_NAME, DEFAULT_BRAND_SLOGAN, brandConfig } = require("./config/brand");

const root = path.resolve(__dirname, "..");

test("public HTML and translations never mention the internal name", () => {
  const files = ["public/index.html", ...fs.readdirSync(path.join(root, "src/i18n/locales")).map((f) => `src/i18n/locales/${f}`)];
  const leaks = files.filter((f) => PATTERN.test(fs.readFileSync(path.join(root, f), "utf8")));
  expect(leaks).toEqual([]);
});

test("built bundle (if present) never mentions the internal name", () => {
  const build = path.join(root, "build");
  if (!fs.existsSync(build)) return;
  expect(findLeaks(build)).toEqual([]);
});

test("brand comes from env with the Mind At Ease Solutions HVAC defaults", () => {
  expect(brandConfig({})).toEqual({ niche: "hvac", name: DEFAULT_BRAND_NAME, slogan: DEFAULT_BRAND_SLOGAN, siteUrl: "https://hvac.mindateasesolutions.com" });
  expect(brandConfig({ REACT_APP_BRAND_NAME: " Acme ", REACT_APP_BRAND_SLOGAN: "Hi" })).toMatchObject({ name: "Acme", slogan: "Hi" });
});

test("source map 'mappings' noise is ignored, but a real leak in a map is still caught", () => {
  const os = require("os");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "leak-"));
  try {
    fs.writeFileSync(path.join(dir, "a.js.map"), JSON.stringify({ version: 3, sources: ["src/a.js"], names: [], mappings: "AAAA,EAAMomO,IAEzB" }));
    expect(findLeaks(dir)).toEqual([]);
    fs.writeFileSync(path.join(dir, "b.js.map"), JSON.stringify({ version: 3, sources: ["src/b.js"], sourcesContent: ["const x = 'Momo';"], mappings: "AAAA" }));
    expect(findLeaks(dir)).toEqual(["b.js.map"]);
    fs.writeFileSync(path.join(dir, "c.js"), "var a='MomO'");
    expect(findLeaks(dir).sort()).toEqual(["b.js.map", "c.js"]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
