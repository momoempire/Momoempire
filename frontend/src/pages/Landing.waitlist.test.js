// EMP-WL-022 guard: the landing waitlist form must keep PR #11's bot checks. A bad merge of PR #4
// (which also edits Landing.jsx) could silently drop them; these tests then fail.
// CRA's Jest has no "@/" alias: Landing's "@/..." imports are mapped to the real modules below
// (Logo, LanguageSwitcher, DemoCall and api are stubbed; TurnstileWidget is the REAL component).
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/utils", () => jest.requireActual("../lib/utils"), { virtual: true });
jest.mock("@/components/ui/button", () => jest.requireActual("../components/ui/button"), { virtual: true });
jest.mock("@/components/ui/badge", () => jest.requireActual("../components/ui/badge"), { virtual: true });
jest.mock("@/components/ui/input", () => jest.requireActual("../components/ui/input"), { virtual: true });
jest.mock("@/components/ui/textarea", () => jest.requireActual("../components/ui/textarea"), { virtual: true });
jest.mock("@/components/TurnstileWidget", () => jest.requireActual("../components/TurnstileWidget"), { virtual: true });
jest.mock("@/components/Logo", () => ({ Logo: () => null }), { virtual: true });
jest.mock("@/components/LanguageSwitcher", () => () => null, { virtual: true });
jest.mock("@/components/DemoCall", () => () => null, { virtual: true });
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(() => Promise.reject(new Error("offline"))), post: jest.fn() },
  errMessage: (e) => String(e),
}), { virtual: true });
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter } = require("react-router");
require("../i18n");
const { api } = require("@/lib/api");
const Landing = require("./Landing").default;

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const TURNSTILE_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const ORIGINAL_KEY = process.env.REACT_APP_TURNSTILE_SITE_KEY;

let container;
let root;
beforeEach(() => {
  // CRA's Jest config resets mock implementations before each test.
  api.get.mockImplementation(() => Promise.reject(new Error("offline")));
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.head.querySelectorAll(`script[src="${TURNSTILE_SRC}"]`).forEach((s) => s.remove());
  delete window.turnstile;
  if (ORIGINAL_KEY === undefined) delete process.env.REACT_APP_TURNSTILE_SITE_KEY;
  else process.env.REACT_APP_TURNSTILE_SITE_KEY = ORIGINAL_KEY;
});

async function renderLanding() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(React.createElement(MemoryRouter, null, React.createElement(Landing)));
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const form = () => container.querySelector('[data-testid="waitlist-submit"]').closest("form");

test("waitlist form renders the hidden honeypot input", async () => {
  delete process.env.REACT_APP_TURNSTILE_SITE_KEY;
  await renderLanding();
  const hp = form().querySelector('input[name="website"]');
  expect(hp).not.toBeNull();
  expect(hp.getAttribute("tabindex")).toBe("-1");
  expect(hp.getAttribute("autocomplete")).toBe("off");
  const wrapper = hp.closest('[aria-hidden="true"]');
  expect(wrapper).not.toBeNull();
  expect(wrapper.style.position).toBe("absolute");
  expect(wrapper.style.left).toBe("-10000px");
  // No site key -> no widget and no Cloudflare script.
  expect(form().querySelector('[data-testid="waitlist-turnstile"]')).toBeNull();
  expect(document.head.querySelector(`script[src="${TURNSTILE_SRC}"]`)).toBeNull();
});

test("with a site key, the Turnstile widget mounts in the waitlist form and gets rendered", async () => {
  process.env.REACT_APP_TURNSTILE_SITE_KEY = "test-site-key";
  const render = jest.fn(() => "widget-1");
  window.turnstile = { render, remove: jest.fn() };
  await renderLanding();
  const mount = form().querySelector('[data-testid="waitlist-turnstile"]');
  expect(mount).not.toBeNull();
  expect(render).toHaveBeenCalledTimes(1);
  expect(render.mock.calls[0][0]).toBe(mount);
  expect(render.mock.calls[0][1].sitekey).toBe("test-site-key");
});

test("with a site key and no Turnstile loaded yet, the Cloudflare script is requested", async () => {
  process.env.REACT_APP_TURNSTILE_SITE_KEY = "test-site-key";
  await renderLanding();
  expect(form().querySelector('[data-testid="waitlist-turnstile"]')).not.toBeNull();
  expect(document.head.querySelector(`script[src="${TURNSTILE_SRC}"]`)).not.toBeNull();
});

test("submitting sends the honeypot and Turnstile token fields", async () => {
  process.env.REACT_APP_TURNSTILE_SITE_KEY = "test-site-key";
  let callback;
  window.turnstile = { render: jest.fn((el, opts) => { callback = opts.callback; return "w"; }), remove: jest.fn() };
  api.post.mockResolvedValue({ data: { ok: true, status: "received" } });
  await renderLanding();
  await act(async () => { callback("tok-abc"); });
  const email = container.querySelector('[data-testid="waitlist-email"]');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(email, "owner@example.com");
    email.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    form().dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(api.post).toHaveBeenCalledTimes(1);
  const [url, body] = api.post.mock.calls[0];
  expect(url).toBe("/public/waitlist");
  expect(body).toMatchObject({ email: "owner@example.com", website: "", turnstile_token: "tok-abc" });
});
