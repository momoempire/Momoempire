// QA (Oct 13): /pricing shows an error + retry when /plans fails, and its copy follows the
// selected language (EN/ES). Same no-RTL harness as Estimate.test.js.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/utils", () => jest.requireActual("../lib/utils"), { virtual: true });
jest.mock("@/i18n", () => jest.requireActual("../i18n"), { virtual: true });
jest.mock("@/i18n/estimator", () => jest.requireActual("../i18n/estimator"), { virtual: true });
jest.mock("@/config/estimatorRules", () => jest.requireActual("../config/estimatorRules"), { virtual: true });
jest.mock("@/config/contact", () => ({ CONTACT_EMAIL: "hello@example.com" }), { virtual: true });
jest.mock("@/components/ui/button", () => jest.requireActual("../components/ui/button"), { virtual: true });
jest.mock("@/components/ui/badge", () => jest.requireActual("../components/ui/badge"), { virtual: true });
jest.mock("@/components/Logo", () => ({ Logo: () => null }), { virtual: true });
jest.mock("@/components/ThemeToggle", () => ({ __esModule: true, default: () => null }), { virtual: true });
jest.mock("@/components/LanguageSwitcher", () => ({ __esModule: true, default: () => null }), { virtual: true });
jest.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: null }) }), { virtual: true });
jest.mock("@/lib/checkout", () => ({
  isPaidCheckoutPlan: () => true, setPendingCheckoutPlan: jest.fn(), startStripeCheckout: jest.fn(),
}), { virtual: true });
jest.mock("@/lib/api", () => ({ api: { get: jest.fn() }, errMessage: (e) => String(e) }), { virtual: true });
jest.mock("sonner", () => ({ toast: { error: jest.fn() } }));

const React = require("react");
const { MemoryRouter } = require("react-router");
const { api } = require("@/lib/api");
const i18n = require("../i18n").default;
const Pricing = require("./Pricing").default;
const { act } = React;
const { createRoot } = require("react-dom/client");
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PLANS = [
  { key: "trial", name: "Trial", price_cents: 0, description: "Try it" },
  { key: "starter", name: "Starter", price_cents: 1999, limits: { ai_minutes: 100, calls: 150, sms: 300 }, overage: { ai_minutes: 25, sms: 2 }, features: [] },
  { key: "enterprise", name: "Enterprise", price_cents: 0 },
];

let container; let root;
afterEach(async () => {
  act(() => root && root.unmount());
  container?.remove(); container = root = undefined;
  await act(async () => { await i18n.changeLanguage("en"); });
});
const q = (id) => document.querySelector(`[data-testid="${id}"]`);
async function flush(n = 5) { for (let i = 0; i < n; i += 1) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); }
async function render() {
  container = document.createElement("div"); document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(React.createElement(MemoryRouter, null, React.createElement(Pricing))));
  await flush();
}

test("shows an error with a working retry when /plans fails", async () => {
  api.get.mockReset();
  api.get.mockRejectedValueOnce(new Error("down")).mockResolvedValueOnce({ data: PLANS });
  await render();
  expect(q("pricing-load-error")).not.toBeNull();
  act(() => q("pricing-retry-btn").click());
  await flush();
  expect(q("pricing-load-error")).toBeNull();
  expect(q("pricing-plan-starter")).not.toBeNull();
  expect(q("pricing-plan-starter").textContent).toMatch(/\$19\.99\/mo/);
  expect(q("pricing-plan-starter").textContent).toMatch(/25¢\/min · 2¢\/SMS/);
});

test("copy follows the selected language (Spanish)", async () => {
  api.get.mockReset();
  api.get.mockResolvedValue({ data: PLANS });
  await act(async () => { await i18n.changeLanguage("es"); });
  await render();
  const text = container.textContent;
  expect(text).toMatch(/Pague por la oficina/);
  expect(text).toMatch(/100 minutos de IA \/ mes/);
  expect(text).toMatch(/Empezar Inicial/);
  expect(text).toMatch(/Contactar a ventas/);
  expect(text).not.toMatch(/Pay for the office|Contact sales|AI minutes/);
});

test("formatPrice keeps cents and whole dollars", () => {
  const { formatPrice } = require("./Pricing");
  expect(formatPrice(1999)).toBe("$19.99");
  expect(formatPrice(5000)).toBe("$50.00");
});
