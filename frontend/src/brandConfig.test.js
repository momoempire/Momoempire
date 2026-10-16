const { brandConfig, brandForNiche, DEFAULT_BRAND_NAME, DEFAULT_SITE_URL } = require("./config/brand");

test("default brand is Mind At Ease Solutions HVAC", () => {
  expect(DEFAULT_BRAND_NAME).toBe("Mind At Ease Solutions HVAC");
  expect(DEFAULT_SITE_URL).toBe("https://hvac.mindateasesolutions.com");
  expect(brandConfig({}).name).toBe("Mind At Ease Solutions HVAC");
});

test("per-niche pattern and overrides", () => {
  expect(brandConfig({ REACT_APP_NICHE: "roofing" })).toMatchObject({
    name: "Mind At Ease Solutions Roofing", siteUrl: "https://roofing.mindateasesolutions.com",
  });
  expect(brandForNiche("pest-control").name).toBe("Mind At Ease Solutions Pest Control");
  expect(brandForNiche("<x>").niche).toBe("hvac");
  expect(brandConfig({ REACT_APP_BRAND_NAME: "X", REACT_APP_SITE_URL: "https://y.test/" })).toMatchObject({ name: "X", siteUrl: "https://y.test" });
});

test("public brand never contains the internal name or legacy domain", () => {
  for (const n of ["hvac", "plumbing", "electrical", "roofing", "landscaping"]) {
    const b = brandForNiche(n);
    expect(`${b.name} ${b.siteUrl}`).not.toMatch(/momo|aioffice/i);
  }
});
