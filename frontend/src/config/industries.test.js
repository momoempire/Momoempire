// Track C (owner audit, 2026-10-17): the landing architecture is ONE shared template
// driven by the 33 trade packs. These tests pin that contract: every pack resolves a
// landing page in EN and ES, slugs map to real backend pack files, unknown slugs get
// no page (the generic face is the fallback, never another trade's page), and no
// trade's copy leaks another trade's name.
const fs = require("fs");
const path = require("path");
const { INDUSTRY_SLUGS, INDUSTRY_PATHS, INDUSTRY_PACK_IDS, industryPage } = require("./industries");
const { brandForNiche, NICHE_LABELS } = require("./brand");
const { BRAND_NAME } = require("./brand");

const PACKS_DIR = path.resolve(__dirname, "../../../backend/trade_packs/packs");

test("all 33 trade packs have a landing slug, and only the 33", () => {
  expect(INDUSTRY_SLUGS).toHaveLength(33);
  expect(new Set(INDUSTRY_SLUGS).size).toBe(33);
  expect(INDUSTRY_PATHS).toHaveLength(66);
  // The original five routes stay first and unchanged.
  expect(INDUSTRY_SLUGS.slice(0, 5)).toEqual(["hvac", "plumbing", "electrical", "roofing", "landscaping"]);
});

test("every slug maps to a real backend trade pack file", () => {
  for (const slug of INDUSTRY_SLUGS) {
    const packId = INDUSTRY_PACK_IDS[slug];
    expect(packId).toBe(slug.replace(/-/g, "_"));
    expect(fs.existsSync(path.join(PACKS_DIR, `${packId}.yaml`))).toBe(true);
  }
  // …and every pack file on disk is reachable from a slug (no orphan packs).
  const onDisk = fs.readdirSync(PACKS_DIR).filter((f) => f.endsWith(".yaml")).map((f) => f.replace(/\.yaml$/, "")).sort();
  expect(Object.values(INDUSTRY_PACK_IDS).sort()).toEqual(onDisk);
});

test.each(INDUSTRY_SLUGS)("%s resolves landing copy in EN and ES under the parent brand", (slug) => {
  for (const lang of ["en", "es"]) {
    const page = industryPage(slug, lang);
    expect(page).not.toBeNull();
    expect(page.slug).toBe(slug);
    expect(page.lang).toBe(lang);
    expect(page.copy.title.length).toBeGreaterThan(0);
    expect(page.copy.sub).toContain(BRAND_NAME);
    expect(page.copy.sub).toContain("Mind At Ease Solutions");
    expect(page.copy.points).toHaveLength(4);
    expect(page.copy.formIndustry).toBe(industryPage(slug, "en").copy.label);
  }
});

test("the directive's switch set resolves: hvac, plumbing, pest-control, real-estate, barbershop", () => {
  for (const slug of ["hvac", "plumbing", "pest-control", "real-estate", "barbershop"]) {
    expect(industryPage(slug, "en").copy.label).toBeTruthy();
    expect(industryPage(slug, "es").copy.label).toBeTruthy();
  }
  expect(industryPage("pest-control", "en").copy.title).toContain("Pest Control");
  expect(industryPage("real-estate", "en").copy.title).toContain("Real Estate");
  expect(industryPage("barbershop", "es").copy.title).toContain("barbería");
});

test("unknown niches get NO industry page (generic face is the fallback)", () => {
  // dental/legal/salon appear as marketing cards on the main landing; they are not
  // trade packs and must not resolve a pack-driven page.
  for (const slug of ["dental", "legal", "salon", "not-a-trade", "", "hvac-2"]) {
    expect(industryPage(slug)).toBeNull();
    expect(industryPage(slug, "es")).toBeNull();
  }
});

test("no trade's landing copy leaks another trade (HVAC wording stays on the HVAC page)", () => {
  for (const slug of INDUSTRY_SLUGS) {
    for (const lang of ["en", "es"]) {
      const { copy } = industryPage(slug, lang);
      const surface = `${copy.title} ${copy.sub} ${copy.label}`;
      if (slug !== "hvac") expect(surface).not.toMatch(/HVAC/);
      expect(surface).not.toMatch(/momo/i);
    }
  }
});

test("every landing slug resolves its canonical parent-brand face", () => {
  for (const slug of INDUSTRY_SLUGS) {
    const face = brandForNiche(slug);
    expect(face.niche).toBe(slug);
    expect(face.name).toBe(`Mind At Ease Solutions ${industryPage(slug, "en").copy.label}`);
    expect(face.siteUrl).toBe(`https://${slug}.mindateasesolutions.com`);
  }
  expect(Object.keys(NICHE_LABELS)).toHaveLength(33);
});
