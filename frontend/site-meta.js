// EMP-WL-014: build-time site metadata. Generates the <head> meta tags (title, description,
// Open Graph, Twitter), robots.txt and sitemap.xml from REACT_APP_* env vars, so nothing about
// the domain, brand or share image is hard-coded. Used by craco.config.js; unit-tested in
// src/siteMeta.test.js.
//
// TODO(Brann): brand name, description, domain and share image are Brann's decision. Until set,
// the existing title/description are used, no sitemap is generated and no og:image is emitted.

const DEFAULT_SITE_NAME = "Empire AI Office"; // TODO(Brann): brand name (current <title> kept)
const DEFAULT_DESCRIPTION = "Empire AI Office platform"; // TODO(Brann): marketing description

const PUBLIC_PAGES = ["/", "/pricing", "/privacy", "/terms"];
const WAITLIST_PAGES = ["/", "/privacy", "/terms"];

function clean(v) {
  return typeof v === "string" ? v.trim() : "";
}

function absoluteUrl(name, raw) {
  const v = clean(raw);
  if (!v) return "";
  let u;
  try {
    u = new URL(v);
  } catch (e) {
    throw new Error(`[EMP-WL-014] ${name} must be an absolute http(s) URL, got "${v}".`);
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") {
    throw new Error(`[EMP-WL-014] ${name} must be an absolute http(s) URL, got "${v}".`);
  }
  return u.toString();
}

function siteConfig(env = process.env) {
  const siteUrl = absoluteUrl("REACT_APP_SITE_URL", env.REACT_APP_SITE_URL).replace(/\/+$/, "");
  return {
    name: clean(env.REACT_APP_SITE_NAME) || DEFAULT_SITE_NAME,
    description: clean(env.REACT_APP_SITE_DESCRIPTION) || DEFAULT_DESCRIPTION,
    siteUrl,
    ogImage: absoluteUrl("REACT_APP_OG_IMAGE_URL", env.REACT_APP_OG_IMAGE_URL),
    waitlistOnly: env.REACT_APP_WAITLIST_ONLY === "true",
  };
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Tags for html-webpack-plugin. It does not escape attribute values, so they are escaped here.
function metaTags(cfg) {
  const m = (attrs) => ({
    tagName: "meta",
    voidTag: true,
    attributes: Object.fromEntries(Object.entries(attrs).map(([k, v]) => [k, escapeHtml(v)])),
    meta: { plugin: "site-meta" },
  });
  const tags = [
    m({ name: "description", content: cfg.description }),
    m({ property: "og:type", content: "website" }),
    m({ property: "og:site_name", content: cfg.name }),
    m({ property: "og:title", content: cfg.name }),
    m({ property: "og:description", content: cfg.description }),
  ];
  if (cfg.siteUrl) tags.push(m({ property: "og:url", content: `${cfg.siteUrl}/` }));
  if (cfg.ogImage) tags.push(m({ property: "og:image", content: cfg.ogImage }));
  tags.push(
    m({ name: "twitter:card", content: cfg.ogImage ? "summary_large_image" : "summary" }),
    m({ name: "twitter:title", content: cfg.name }),
    m({ name: "twitter:description", content: cfg.description }),
  );
  if (cfg.ogImage) tags.push(m({ name: "twitter:image", content: cfg.ogImage }));
  return tags;
}

function withTitle(html, cfg) {
  return html.replace(/<title>[^<]*<\/title>/, `<title>${escapeHtml(cfg.name)}</title>`);
}

function sitemapPages(cfg) {
  return cfg.waitlistOnly ? WAITLIST_PAGES : PUBLIC_PAGES;
}

function robotsTxt(cfg) {
  const lines = ["User-agent: *"];
  if (!cfg.waitlistOnly) {
    // Signed-in app, auth flows and token links: nothing for a crawler there.
    for (const p of ["/app", "/admin", "/onboarding", "/login", "/signup", "/forgot-password",
      "/reset-password", "/auth/", "/invite", "/payment/", "/portal/", "/reviews/", "/t/", "/r/"]) {
      lines.push(`Disallow: ${p}`);
    }
  }
  lines.push("Allow: /");
  if (cfg.siteUrl) lines.push("", `Sitemap: ${cfg.siteUrl}/sitemap.xml`);
  return lines.join("\n") + "\n";
}

function sitemapXml(cfg) {
  if (!cfg.siteUrl) return null; // a sitemap needs absolute URLs
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const urls = sitemapPages(cfg).map((p) => `  <url><loc>${esc(cfg.siteUrl + p)}</loc></url>`);
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ...urls,
    "</urlset>",
    "",
  ].join("\n");
}

// Webpack plugin: adds the meta tags to index.html and emits robots.txt (+ sitemap.xml).
class SiteMetaPlugin {
  constructor(cfg) {
    this.cfg = cfg;
  }

  apply(compiler) {
    const HtmlWebpackPlugin = require("html-webpack-plugin");
    const { RawSource } = compiler.webpack.sources;
    const { Compilation } = compiler.webpack;
    compiler.hooks.thisCompilation.tap("SiteMetaPlugin", (compilation) => {
      HtmlWebpackPlugin.getHooks(compilation).alterAssetTagGroups.tap("SiteMetaPlugin", (data) => {
        data.headTags.unshift(...metaTags(this.cfg));
        return data;
      });
      HtmlWebpackPlugin.getHooks(compilation).beforeEmit.tap("SiteMetaPlugin", (data) => {
        data.html = withTitle(data.html, this.cfg);
        return data;
      });
      compilation.hooks.processAssets.tap(
        { name: "SiteMetaPlugin", stage: Compilation.PROCESS_ASSETS_STAGE_ADDITIONAL },
        () => {
          compilation.emitAsset("robots.txt", new RawSource(robotsTxt(this.cfg)));
          const xml = sitemapXml(this.cfg);
          if (xml) compilation.emitAsset("sitemap.xml", new RawSource(xml));
        },
      );
    });
  }
}

module.exports = {
  DEFAULT_SITE_NAME,
  DEFAULT_DESCRIPTION,
  siteConfig,
  metaTags,
  withTitle,
  escapeHtml,
  robotsTxt,
  sitemapXml,
  sitemapPages,
  SiteMetaPlugin,
};
