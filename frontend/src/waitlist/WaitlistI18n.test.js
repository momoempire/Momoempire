// EMP-WL-093: the waitlist-only build bundles only the waitlist strings. The bundle itself is
// checked by building (no "Start free trial" etc. in main.js); this covers the subset files and
// renders every waitlist page with ONLY those strings loaded.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(() => Promise.resolve({ data: [] })), post: jest.fn(), put: jest.fn() },
  errMessage: (e) => String(e),
}));

const fs = require("fs");
const path = require("path");

const LOCALES = path.join(__dirname, "..", "i18n", "locales");
const read = (f) => JSON.parse(fs.readFileSync(path.join(LOCALES, f), "utf8"));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const ORIGINAL = process.env.REACT_APP_WAITLIST_ONLY;
afterEach(() => {
  if (ORIGINAL === undefined) delete process.env.REACT_APP_WAITLIST_ONLY;
  else process.env.REACT_APP_WAITLIST_ONLY = ORIGINAL;
});

test.each(["en", "es"])("waitlist.%s.json = the 'waitlist' landing keys of %s.json, nothing else", (lang) => {
  const full = read(`${lang}.json`);
  const expected = { landing: Object.fromEntries(Object.entries(full.landing).filter(([k]) => k.includes("waitlist"))) };
  expect(read(`waitlist.${lang}.json`)).toEqual(expected);
  const text = JSON.stringify(read(`waitlist.${lang}.json`));
  expect(text).not.toMatch(/trial|prueba|pricing|precio|log ?in|sign ?up|billing|factura|24\/7|stripe/i);
});

test.each(["/", "/privacy", "/terms"])("%s renders with only the waitlist strings (no missing keys)", async (route) => {
  process.env.REACT_APP_WAITLIST_ONLY = "true";
  let html = "";
  let loaded;
  jest.resetModules(); // fresh i18n (and React tree) loaded with the waitlist flag on
  {
    const React = require("react");
    const { act } = React;
    const { createRoot } = require("react-dom/client");
    const { MemoryRouter } = require("react-router");
    const i18n = require("@/i18n").default;
    loaded = i18n.getResourceBundle("en", "translation");
    const { WaitlistRoutes } = require("@/waitlist/WaitlistApp");
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<MemoryRouter initialEntries={[route]}><WaitlistRoutes /></MemoryRouter>);
    });
    html = container.textContent;
    act(() => root.unmount());
    container.remove();
  }
  expect(Object.keys(loaded).sort()).toEqual(["landing", "waitlistErrors"].filter((k) => k in loaded).sort());
  expect(loaded.landing.cta_trial).toBeUndefined();
  expect(loaded.common).toBeUndefined();
  expect(html).not.toMatch(/\b(landing|common|nav|billing|waitlistErrors)\.[a-z_]+/); // i18next prints missing keys
  if (route === "/") expect(html).toContain("Join waitlist");
});
