// EMP-WL-072 (canonical link, favicon.ico) and EMP-WL-073 (no source maps in the waitlist build).
// The source-map setting is also checked by building (see PR); this covers the config switch.
const fs = require("fs");
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });
const path = require("path");
const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter, useNavigate } = require("react-router");

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
const { default: CanonicalLink, canonicalHref } = require("../components/CanonicalLink");

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const FRONTEND = path.join(__dirname, "..", "..");
const ENV = { ...process.env };
afterEach(() => {
  process.env = { ...ENV };
  document.head.querySelectorAll('link[rel="canonical"]').forEach((l) => l.remove());
});

describe("canonicalHref", () => {
  test.each([
    ["https://example.test", "/", "https://example.test/"],
    ["https://example.test/", "/privacy", "https://example.test/privacy"],
    [" https://example.test// ", "/terms/", "https://example.test/terms"],
    ["https://example.test/base", "/", "https://example.test/base/"],
  ])("%s + %s", (site, p, expected) => {
    expect(canonicalHref(site, p)).toBe(expected);
  });
  test.each([[undefined], [""], ["example.test"], ["/relative"], ["javascript:alert(1)"]])("no tag for %p", (site) => {
    expect(canonicalHref(site, "/")).toBe("");
  });
});

describe("<CanonicalLink />", () => {
  let navigate;
  function Nav() {
    navigate = useNavigate();
    return null;
  }
  async function render(initial) {
    const container = document.createElement("div");
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemoryRouter initialEntries={[initial]}><CanonicalLink /><Nav /></MemoryRouter>);
    });
    return root;
  }
  const links = () => [...document.head.querySelectorAll('link[rel="canonical"]')];

  test("one canonical per route, from REACT_APP_SITE_URL, without query or hash", async () => {
    process.env.REACT_APP_SITE_URL = "https://example.test";
    const root = await render("/privacy?utm_source=x#top");
    expect(links().map((l) => l.href)).toEqual(["https://example.test/privacy"]);
    await act(async () => navigate("/terms"));
    expect(links().map((l) => l.href)).toEqual(["https://example.test/terms"]);
    await act(async () => navigate("/"));
    expect(links().map((l) => l.href)).toEqual(["https://example.test/"]);
    act(() => root.unmount());
  });

  test("no REACT_APP_SITE_URL: no canonical tag (domain is TODO(Brann))", async () => {
    delete process.env.REACT_APP_SITE_URL;
    const root = await render("/");
    expect(links()).toEqual([]);
    act(() => root.unmount());
  });

  test("mounted in both app roots", () => {
    for (const f of ["src/App.js", "src/waitlist/WaitlistApp.jsx"]) {
      expect(fs.readFileSync(path.join(FRONTEND, f), "utf8")).toMatch(/<BrowserRouter>\s*<CanonicalLink \/>/);
    }
  });
});

describe("favicon.ico", () => {
  test("is a real ICO holding the 32x32 PNG (no more SPA fallback HTML)", () => {
    const ico = fs.readFileSync(path.join(FRONTEND, "public", "favicon.ico"));
    const png = fs.readFileSync(path.join(FRONTEND, "public", "favicon-32.png"));
    expect([ico.readUInt16LE(0), ico.readUInt16LE(2), ico.readUInt16LE(4)]).toEqual([0, 1, 1]);
    expect([ico[6], ico[7]]).toEqual([32, 32]);
    const size = ico.readUInt32LE(14);
    const offset = ico.readUInt32LE(18);
    expect(offset).toBe(22);
    expect(Buffer.compare(ico.subarray(offset, offset + size), png)).toBe(0);
    expect(png.subarray(1, 4).toString()).toBe("PNG");
  });
});

describe("source maps (EMP-WL-073)", () => {
  const load = () => jest.isolateModules(() => require("../../craco.config.js"));
  test("waitlist-only build: GENERATE_SOURCEMAP is forced to false, even if set to true", () => {
    process.env.REACT_APP_WAITLIST_ONLY = "true";
    process.env.GENERATE_SOURCEMAP = "true";
    load();
    expect(process.env.GENERATE_SOURCEMAP).toBe("false");
  });
  test("full build: left as configured", () => {
    delete process.env.REACT_APP_WAITLIST_ONLY;
    delete process.env.GENERATE_SOURCEMAP;
    load();
    expect(process.env.GENERATE_SOURCEMAP).toBeUndefined();
  });
});
