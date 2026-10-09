// EMP-WL-014: site metadata, robots.txt and sitemap.xml generation (frontend/site-meta.js) and
// the cleaned-up public/index.html. The webpack wiring is exercised by the builds.
const fs = require("fs");
const path = require("path");
const {
  siteConfig, metaTags, withTitle, robotsTxt, sitemapXml, DEFAULT_SITE_NAME, DEFAULT_DESCRIPTION,
} = require("../site-meta");

const PUBLIC = path.join(__dirname, "..", "public");
const attrs = (tags) => tags.map((t) => t.attributes);
const find = (tags, key, value) => attrs(tags).find((a) => a[key] === value);

describe("siteConfig", () => {
  test("defaults keep the existing title/description and need no domain", () => {
    const cfg = siteConfig({});
    expect(cfg).toEqual({
      name: DEFAULT_SITE_NAME, description: DEFAULT_DESCRIPTION, siteUrl: "", ogImage: "", waitlistOnly: false,
    });
  });

  test("site URL is normalised (no trailing slash) and must be absolute http(s)", () => {
    expect(siteConfig({ REACT_APP_SITE_URL: " https://example.test/ " }).siteUrl).toBe("https://example.test");
    expect(siteConfig({ REACT_APP_SITE_URL: "https://example.test/base//" }).siteUrl).toBe("https://example.test/base");
    for (const bad of ["example.test", "/relative", "ftp://example.test", "javascript:alert(1)"]) {
      expect(() => siteConfig({ REACT_APP_SITE_URL: bad })).toThrow(/EMP-WL-014.*REACT_APP_SITE_URL/);
    }
    expect(() => siteConfig({ REACT_APP_OG_IMAGE_URL: "og.png" })).toThrow(/REACT_APP_OG_IMAGE_URL/);
  });
});

describe("meta tags", () => {
  test("without a domain or image: no og:url / og:image, small Twitter card", () => {
    const tags = metaTags(siteConfig({}));
    expect(find(tags, "name", "description").content).toBe(DEFAULT_DESCRIPTION);
    expect(find(tags, "property", "og:title").content).toBe(DEFAULT_SITE_NAME);
    expect(find(tags, "property", "og:url")).toBeUndefined();
    expect(find(tags, "property", "og:image")).toBeUndefined();
    expect(find(tags, "name", "twitter:image")).toBeUndefined();
    expect(find(tags, "name", "twitter:card").content).toBe("summary");
  });

  test("with domain and image: og:url, og:image, large Twitter card", () => {
    const tags = metaTags(siteConfig({
      REACT_APP_SITE_URL: "https://example.test",
      REACT_APP_OG_IMAGE_URL: "https://cdn.example.test/og.png",
      REACT_APP_SITE_NAME: "Brand",
      REACT_APP_SITE_DESCRIPTION: "Desc",
    }));
    expect(find(tags, "property", "og:url").content).toBe("https://example.test/");
    expect(find(tags, "property", "og:image").content).toBe("https://cdn.example.test/og.png");
    expect(find(tags, "name", "twitter:image").content).toBe("https://cdn.example.test/og.png");
    expect(find(tags, "name", "twitter:card").content).toBe("summary_large_image");
    expect(find(tags, "property", "og:site_name").content).toBe("Brand");
    expect(find(tags, "name", "twitter:description").content).toBe("Desc");
  });

  test("values are HTML-escaped (html-webpack-plugin does not escape attributes)", () => {
    const cfg = siteConfig({ REACT_APP_SITE_NAME: 'A "B" <C> & D', REACT_APP_SITE_DESCRIPTION: '"><script>x</script>' });
    for (const a of attrs(metaTags(cfg))) expect(a.content).not.toMatch(/[<>"]/);
    expect(find(metaTags(cfg), "property", "og:title").content).toBe("A &quot;B&quot; &lt;C&gt; &amp; D");
    expect(withTitle("<head><title>Old</title></head>", cfg)).toBe("<head><title>A &quot;B&quot; &lt;C&gt; &amp; D</title></head>");
  });
});

describe("robots.txt and sitemap.xml", () => {
  test("no site URL: robots allows all, no Sitemap line, no sitemap.xml", () => {
    const cfg = siteConfig({ REACT_APP_WAITLIST_ONLY: "true" });
    expect(robotsTxt(cfg)).toBe("User-agent: *\nAllow: /\n");
    expect(sitemapXml(cfg)).toBeNull();
  });

  test("waitlist build lists only /, /privacy and /terms", () => {
    const cfg = siteConfig({ REACT_APP_WAITLIST_ONLY: "true", REACT_APP_SITE_URL: "https://example.test/" });
    expect(robotsTxt(cfg)).toBe("User-agent: *\nAllow: /\n\nSitemap: https://example.test/sitemap.xml\n");
    const xml = sitemapXml(cfg);
    const locs = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
    expect(locs).toEqual(["https://example.test/", "https://example.test/privacy", "https://example.test/terms"]);
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')).toBe(true);
  });

  test("normal build adds /pricing and keeps crawlers out of the app, auth and token links", () => {
    const cfg = siteConfig({ REACT_APP_SITE_URL: "https://example.test" });
    const locs = [...sitemapXml(cfg).matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
    expect(locs).toEqual(["/", "/pricing", "/privacy", "/terms"].map((p) => `https://example.test${p}`));
    const robots = robotsTxt(cfg);
    for (const p of ["/app", "/admin", "/login", "/signup", "/reset-password", "/invite", "/reviews/", "/portal/"]) {
      expect(robots).toContain(`Disallow: ${p}\n`);
    }
    expect(robots).toContain("Sitemap: https://example.test/sitemap.xml");
  });

  test("public/ has no static robots.txt or sitemap.xml that would shadow the generated ones", () => {
    expect(fs.existsSync(path.join(PUBLIC, "robots.txt"))).toBe(false);
    expect(fs.existsSync(path.join(PUBLIC, "sitemap.xml"))).toBe(false);
  });
});

describe("public/index.html", () => {
  const html = fs.readFileSync(path.join(PUBLIC, "index.html"), "utf8");

  test("favicon links point at files that exist", () => {
    const hrefs = [...html.matchAll(/<link rel="(?:icon|apple-touch-icon)" href="%PUBLIC_URL%\/([^"]+)"/g)].map((m) => m[1]);
    expect(hrefs).toEqual(["favicon.svg", "favicon-32.png", "apple-touch-icon.png"]);
    for (const f of hrefs) expect(fs.existsSync(path.join(PUBLIC, f))).toBe(true);
    const png = fs.readFileSync(path.join(PUBLIC, "apple-touch-icon.png"));
    expect(png.readUInt32BE(16)).toBe(180); // width
    expect(fs.readFileSync(path.join(PUBLIC, "favicon-32.png")).readUInt32BE(16)).toBe(32);
  });

  test("no CRA/Emergent template leftovers, no unused font, no hard-coded description", () => {
    expect(html).not.toMatch(/manifest\.json|npm run build|yarn build|This HTML file is a template|webfonts/i);
    expect(html).not.toMatch(/emergent/i);
    expect(html).not.toMatch(/fonts\.googleapis|family=Inter/);
    expect(html).not.toMatch(/<meta name="description"/); // injected from env at build time
    expect(html).toMatch(/<title>[^<]+<\/title>/); // replaced at build time
  });
});
