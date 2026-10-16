// Light/dark toggle (next-themes): system default, toggling, persistence, a11y, no-flash script.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });
const fs = require("fs");
const path = require("path");
const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const ThemeProvider = require("./ThemeProvider").default;
const ThemeToggle = require("./ThemeToggle").default;

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function mockSystem(dark) {
  window.matchMedia = jest.fn().mockImplementation((q) => ({
    matches: dark && q.includes("dark"), media: q, onchange: null,
    addListener: jest.fn(), removeListener: jest.fn(), addEventListener: jest.fn(), removeEventListener: jest.fn(), dispatchEvent: jest.fn(),
  }));
}
const html = () => document.documentElement;
let cleanup = null;
function mount(lang) {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el);
  act(() => { root.render(<ThemeProvider><ThemeToggle lang={lang} /></ThemeProvider>); });
  cleanup = () => { act(() => root.unmount()); el.remove(); };
  return el.querySelector('[data-testid="theme-toggle"]');
}
const click = (b) => act(() => { b.click(); });

beforeEach(() => { localStorage.clear(); html().className = ""; html().removeAttribute("style"); });
afterEach(() => { if (cleanup) cleanup(); cleanup = null; });

test("follows the system theme by default (light)", () => {
  mockSystem(false);
  const b = mount();
  expect(b.getAttribute("aria-label")).toBe("Switch to dark theme");
  expect(b.getAttribute("aria-pressed")).toBe("false");
  expect(html().classList.contains("dark")).toBe(false);
});

test("follows the system theme by default (dark)", () => {
  mockSystem(true);
  const b = mount();
  expect(b.getAttribute("aria-label")).toBe("Switch to light theme");
  expect(b.getAttribute("aria-pressed")).toBe("true");
  expect(html().classList.contains("dark")).toBe(true);
});

test("click toggles <html class>, the label, and saves the choice", () => {
  mockSystem(false);
  const b = mount();
  click(b);
  expect(html().classList.contains("dark")).toBe(true);
  expect(localStorage.getItem("theme")).toBe("dark");
  expect(b.getAttribute("aria-label")).toBe("Switch to light theme");
  click(b);
  expect(html().classList.contains("dark")).toBe(false);
  expect(localStorage.getItem("theme")).toBe("light");
});

test("is a native, focusable button (Enter/Space work natively) with a decorative icon", () => {
  mockSystem(false);
  const b = mount();
  expect(b.tagName).toBe("BUTTON");
  expect(b.getAttribute("type")).toBe("button");
  b.focus();
  expect(document.activeElement).toBe(b);
  expect(b.querySelector("svg").getAttribute("aria-hidden")).toBe("true");
});

test("a saved choice wins over the system setting", () => {
  mockSystem(true);
  localStorage.setItem("theme", "light");
  mount();
  expect(html().classList.contains("dark")).toBe(false);
});

test("Spanish label", () => {
  mockSystem(false);
  expect(mount("es").getAttribute("aria-label")).toBe("Cambiar a tema oscuro");
});

test("public/index.html sets the theme class in <head> before first paint, same key", () => {
  const src = fs.readFileSync(path.join(__dirname, "../../public/index.html"), "utf8");
  const m = src.match(/<script>([\s\S]*?)<\/script>/);
  expect(m).toBeTruthy();
  expect(src.indexOf("<script>")).toBeLessThan(src.indexOf("</head>"));
  mockSystem(true);
  new Function(m[1])(); // eslint-disable-line no-new-func
  expect(html().classList.contains("dark")).toBe(true);
  html().className = "";
  localStorage.setItem("theme", "light");
  new Function(m[1])(); // eslint-disable-line no-new-func
  expect(html().classList.contains("dark")).toBe(false);
});
