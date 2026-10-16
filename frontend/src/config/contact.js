// Public contact address and site URL, set at build time. No real domain is hard-coded:
// the default is a reserved placeholder (example.com) until Brann picks the real ones.
const { brandConfig } = require("./brand");
const DEFAULT_CONTACT_EMAIL = "contact@example.com";

function contactConfig(env = process.env) {
  const email = typeof env.REACT_APP_CONTACT_EMAIL === "string" ? env.REACT_APP_CONTACT_EMAIL.trim() : "";
  const site = typeof env.REACT_APP_SITE_URL === "string" ? env.REACT_APP_SITE_URL.trim().replace(/\/+$/, "") : "";
  return { email: email || DEFAULT_CONTACT_EMAIL, siteUrl: site || brandConfig(env).siteUrl };
}

// CRA inlines process.env.REACT_APP_* only when referenced literally.
const CONTACT = contactConfig({
  REACT_APP_CONTACT_EMAIL: process.env.REACT_APP_CONTACT_EMAIL,
  REACT_APP_SITE_URL: process.env.REACT_APP_SITE_URL,
  REACT_APP_NICHE: process.env.REACT_APP_NICHE,
});
const CONTACT_EMAIL = CONTACT.email;
const SITE_URL = CONTACT.siteUrl;

module.exports = { DEFAULT_CONTACT_EMAIL, contactConfig, CONTACT_EMAIL, SITE_URL };
