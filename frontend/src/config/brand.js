// Public brand, set at build time. Pattern: "Mind At Ease Solutions <Niche>" served from
// https://<niche>.mindateasesolutions.com. The niche comes from REACT_APP_NICHE (default "hvac").
// REACT_APP_BRAND_NAME / REACT_APP_SITE_URL still override. The brand name is never translated.
// Kept as plain CommonJS so craco/site-meta.js (Node) and the app share one source of defaults.
const BRAND_ROOT = "Mind At Ease Solutions";
const BRAND_DOMAIN = "mindateasesolutions.com";
const DEFAULT_NICHE = "hvac";
// Display label per niche slug; unknown slugs are title-cased.
const NICHE_LABELS = {
  hvac: "HVAC",
  plumbing: "Plumbing",
  electrical: "Electrical",
  roofing: "Roofing",
  landscaping: "Landscaping",
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
  const label = NICHE_LABELS[niche] || niche.replace(/(^|-)([a-z])/g, (_, d, c) => (d ? " " : "") + c.toUpperCase());
  return { niche, name: `${BRAND_ROOT} ${label}`, siteUrl: `https://${niche}.${BRAND_DOMAIN}` };
}

const DEFAULT_BRAND_NAME = brandForNiche(DEFAULT_NICHE).name;
const DEFAULT_SITE_URL = brandForNiche(DEFAULT_NICHE).siteUrl;

function brandConfig(env = process.env) {
  const nb = brandForNiche(env.REACT_APP_NICHE);
  return {
    niche: nb.niche,
    name: clean(env.REACT_APP_BRAND_NAME) || nb.name,
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
const BRAND_SLOGAN = BRAND.slogan;
const BRAND_SITE_URL = BRAND.siteUrl;

module.exports = {
  BRAND_ROOT, BRAND_DOMAIN, DEFAULT_NICHE, NICHE_LABELS, DEFAULT_BRAND_NAME, DEFAULT_BRAND_SLOGAN,
  DEFAULT_SITE_URL, brandForNiche, brandConfig, BRAND_NAME, BRAND_SLOGAN, BRAND_SITE_URL,
};
