const {
  brandConfig, brandForNiche, DEFAULT_NICHE, GENERIC_NICHE, GENERIC_DESCRIPTOR,
  DEFAULT_BRAND_NAME, DEFAULT_SITE_URL,
} = require("./config/brand");

test("default brand is the generic parent face", () => {
  expect(DEFAULT_NICHE).toBe("generic");
  expect(DEFAULT_BRAND_NAME).toBe("Mind At Ease Solutions");
  expect(DEFAULT_SITE_URL).toBe("https://mindateasesolutions.com");
  expect(brandConfig({}).name).toBe("Mind At Ease Solutions");
  expect(brandConfig({}).niche).toBe("generic");
});

test("generic face: parent name, generic descriptor, neutral accent and glyph", () => {
  const g = brandForNiche(GENERIC_NICHE);
  expect(g).toMatchObject({
    niche: "generic",
    name: "Mind At Ease Solutions",
    descriptor: "AI Office Assistant",
    siteUrl: "https://mindateasesolutions.com",
  });
  expect(g.descriptor).toBe(GENERIC_DESCRIPTOR);
  expect(g.accent).toBeTruthy();
  expect(g.glyph).toBeTruthy();
});

// Guard (books tradition): the generic default surface must never carry a trade —
// a future pack/brand edit cannot silently re-leak one into the parent face.
test("generic default surface contains no trade-specific terms", () => {
  const TRADE_MARKERS = [
    "hvac", "heating", "air conditioning", "furnace", "thermostat", "snowflake",
    "plumbing", "plumber", "electrical", "electrician", "roofing", "landscaping",
    "pest control", "garage door",
  ];
  const cfg = brandConfig({});
  const surface = [cfg.name, cfg.descriptor, cfg.siteUrl, DEFAULT_BRAND_NAME, DEFAULT_SITE_URL]
    .join(" ").toLowerCase();
  for (const m of TRADE_MARKERS) expect(surface).not.toContain(m);
});

test("per-niche pattern and overrides", () => {
  expect(brandConfig({ REACT_APP_NICHE: "roofing" })).toMatchObject({
    name: "Mind At Ease Solutions Roofing", siteUrl: "https://roofing.mindateasesolutions.com",
  });
  expect(brandForNiche("hvac")).toMatchObject({
    niche: "hvac", name: "Mind At Ease Solutions HVAC", siteUrl: "https://hvac.mindateasesolutions.com",
  });
  expect(brandForNiche("pest-control").name).toBe("Mind At Ease Solutions Pest Control");
  expect(brandForNiche("<x>").niche).toBe("generic");
  expect(brandConfig({ REACT_APP_BRAND_NAME: "X", REACT_APP_SITE_URL: "https://y.test/" })).toMatchObject({ name: "X", siteUrl: "https://y.test" });
});

test("all 33 trade-pack niches resolve a canonical parent-brand face", () => {
  const { NICHE_LABELS } = require("./config/brand");
  expect(Object.keys(NICHE_LABELS)).toHaveLength(33);
  for (const [slug, label] of Object.entries(NICHE_LABELS)) {
    const b = brandForNiche(slug);
    expect(b.name).toBe(`Mind At Ease Solutions ${label}`);
    expect(b.siteUrl).toBe(`https://${slug}.mindateasesolutions.com`);
    expect(`${b.name} ${b.siteUrl}`).not.toMatch(/momo|aioffice/i);
  }
  expect(brandForNiche("pest-control").name).toBe("Mind At Ease Solutions Pest Control");
  expect(brandForNiche("real-estate").name).toBe("Mind At Ease Solutions Real Estate");
  expect(brandForNiche("barbershop").name).toBe("Mind At Ease Solutions Barbershop");
});

test("public brand never contains the internal name or legacy domain", () => {
  for (const n of ["generic", "hvac", "plumbing", "electrical", "roofing", "landscaping"]) {
    const b = brandForNiche(n);
    expect(`${b.name} ${b.siteUrl}`).not.toMatch(/momo|aioffice/i);
  }
});
