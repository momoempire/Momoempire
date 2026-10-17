// The old domain must not ship. Sources are always checked; a build is checked when present.
const fs = require("fs");
const path = require("path");
const { findLegacyDomain, PATTERN } = require("../scripts/check-no-legacy-domain");
const { contactConfig, DEFAULT_CONTACT_EMAIL } = require("./config/contact");

const root = path.resolve(__dirname, "..");

test("src and public never contain the old domain", () => {
  const hits = [...findLegacyDomain(path.join(root, "src")), ...findLegacyDomain(path.join(root, "public"))]
    .filter((f) => !f.endsWith("legacyDomain.test.js"));
  expect(hits).toEqual([]);
});

test("built bundle (if present) never contains the old domain", () => {
  const build = path.join(root, "build");
  if (!fs.existsSync(build)) return;
  expect(findLegacyDomain(build)).toEqual([]);
});

test("contact email and site URL come from env with a placeholder email and the brand site URL default", () => {
  expect(contactConfig({})).toEqual({ email: DEFAULT_CONTACT_EMAIL, siteUrl: "https://mindateasesolutions.com" });
  expect(DEFAULT_CONTACT_EMAIL).toMatch(/@example\.com$/);
  expect(PATTERN.test(DEFAULT_CONTACT_EMAIL)).toBe(false);
  expect(contactConfig({ REACT_APP_CONTACT_EMAIL: " hi@test.invalid ", REACT_APP_SITE_URL: "https://x.test/" }))
    .toEqual({ email: "hi@test.invalid", siteUrl: "https://x.test" });
});
