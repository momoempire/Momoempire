// Public brand, set at build time. Trade faces are "Mind At Ease Solutions <Niche>" served
// from https://<niche>.mindateasesolutions.com; with NO niche selected the face is the
// GENERIC parent brand — plain "Mind At Ease Solutions" on the apex domain, no trade
// attached (the same default-face pattern as the bookkeeping app's GENERIC_PACK).
// The niche comes from REACT_APP_NICHE (default "generic"). REACT_APP_BRAND_NAME /
// REACT_APP_SITE_URL still override. The brand name is never translated.
// Kept as plain CommonJS so craco/site-meta.js (Node) and the app share one source of defaults.
const BRAND_ROOT = "Mind At Ease Solutions";
const BRAND_DOMAIN = "mindateasesolutions.com";

// The generic parent face: parent name, generic descriptor, neutral accent, neutral
// glyph. NO trade-specific wording, imagery or terminology — a future pack edit must
// not leak a trade in here (guarded by brandConfig.test.js / brandLeak.test.js).
const GENERIC_NICHE = "generic";
const GENERIC_DESCRIPTOR = "AI Office Assistant";
const GENERIC_ACCENT = "#334155"; // neutral slate
const GENERIC_GLYPH = "Sparkles"; // neutral glyph — the landing chrome's neutral fallback
const GENERIC_BRAND = {
  niche: GENERIC_NICHE,
  name: BRAND_ROOT,
  descriptor: GENERIC_DESCRIPTOR,
  siteUrl: `https://${BRAND_DOMAIN}`,
  accent: GENERIC_ACCENT,
  glyph: GENERIC_GLYPH,
};

const DEFAULT_NICHE = GENERIC_NICHE;
// Display label per niche slug; unknown slugs are title-cased. Covers every one of the
// 33 trade packs (slug = pack id hyphenated; see config/industries.js INDUSTRY_PACK_IDS).
const NICHE_LABELS = {
  hvac: "HVAC",
  plumbing: "Plumbing",
  electrical: "Electrical",
  roofing: "Roofing",
  landscaping: "Landscaping",
  "appliance-repair": "Appliance Repair",
  "auto-repair": "Auto Repair",
  barbershop: "Barbershop",
  cleaning: "Cleaning",
  "decks-outdoor": "Decks & Outdoor Living",
  "fencing-concrete": "Fencing & Concrete",
  flooring: "Flooring",
  "foundation-repair": "Foundation Repair",
  freelancer: "Freelancer",
  "garage-door": "Garage Door",
  generators: "Whole-Home Generators",
  handyman: "Handyman",
  "home-inspection": "Home Inspection",
  irrigation: "Irrigation",
  "junk-removal": "Junk Removal",
  "kitchen-bath": "Kitchen & Bath Remodeling",
  locksmith: "Locksmith",
  painting: "Painting",
  "pest-control": "Pest Control",
  "pool-service": "Pool Service",
  "real-estate": "Real Estate",
  restoration: "Restoration",
  "septic-well": "Septic & Well",
  "siding-gutters": "Siding & Gutters",
  "smart-home": "Smart Home & Security",
  solar: "Solar",
  "tree-service": "Tree Service",
  "windows-doors": "Windows & Doors",
};
const DEFAULT_BRAND_SLOGAN = "Your slogan here";

function clean(v) {
  return typeof v === "string" ? v.trim() : "";
}

function nicheSlug(raw) {
  const s = clean(raw).toLowerCase();
  return /^[a-z0-9-]+$/.test(s) ? s : DEFAULT_NICHE;
}

function brandForNiche(raw) {
  const niche = nicheSlug(raw);
  if (niche === GENERIC_NICHE) return { ...GENERIC_BRAND };
  const label = NICHE_LABELS[niche] || niche.replace(/(^|-)([a-z])/g, (_, d, c) => (d ? " " : "") + c.toUpperCase());
  // Trade faces take their accent/icon from their trade pack, not the brand config.
  return { niche, name: `${BRAND_ROOT} ${label}`, descriptor: label, siteUrl: `https://${niche}.${BRAND_DOMAIN}`, accent: null, glyph: null };
}

const DEFAULT_BRAND_NAME = brandForNiche(DEFAULT_NICHE).name;
const DEFAULT_SITE_URL = brandForNiche(DEFAULT_NICHE).siteUrl;

function brandConfig(env = process.env) {
  const nb = brandForNiche(env.REACT_APP_NICHE);
  return {
    niche: nb.niche,
    name: clean(env.REACT_APP_BRAND_NAME) || nb.name,
    descriptor: nb.descriptor,
    slogan: clean(env.REACT_APP_BRAND_SLOGAN) || DEFAULT_BRAND_SLOGAN,
    siteUrl: clean(env.REACT_APP_SITE_URL).replace(/\/+$/, "") || nb.siteUrl,
  };
}

// CRA inlines process.env.REACT_APP_* only when referenced literally, so read them here directly.
const BRAND = brandConfig({
  REACT_APP_NICHE: process.env.REACT_APP_NICHE,
  REACT_APP_BRAND_NAME: process.env.REACT_APP_BRAND_NAME,
  REACT_APP_BRAND_SLOGAN: process.env.REACT_APP_BRAND_SLOGAN,
  REACT_APP_SITE_URL: process.env.REACT_APP_SITE_URL,
});
const BRAND_NAME = BRAND.name;
const BRAND_DESCRIPTOR = BRAND.descriptor;
const BRAND_SLOGAN = BRAND.slogan;
const BRAND_SITE_URL = BRAND.siteUrl;

module.exports = {
  BRAND_ROOT, BRAND_DOMAIN, GENERIC_NICHE, GENERIC_DESCRIPTOR, GENERIC_ACCENT, GENERIC_GLYPH,
  GENERIC_BRAND, DEFAULT_NICHE, NICHE_LABELS, DEFAULT_BRAND_NAME, DEFAULT_BRAND_SLOGAN,
  DEFAULT_SITE_URL, brandForNiche, brandConfig, BRAND_NAME, BRAND_DESCRIPTOR, BRAND_SLOGAN, BRAND_SITE_URL,
};
