// Page-level tests for /estimate (Watcher review of PR #12: EMP-W-CF-029, EMP-W-CF-030, AI minutes,
// input validation). CRA's Jest has no "@/" alias, so the page's "@/..." imports are mapped to the
// real modules below (api and Logo are stubbed; Turnstile is stubbed to hand back a token).
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/utils", () => jest.requireActual("../lib/utils"), { virtual: true });
jest.mock("@/i18n", () => jest.requireActual("../i18n"), { virtual: true });
jest.mock("@/i18n/estimator", () => jest.requireActual("../i18n/estimator"), { virtual: true });
jest.mock("@/config/estimatorRules", () => jest.requireActual("../config/estimatorRules"), { virtual: true });
jest.mock("@/lib/estimator", () => jest.requireActual("../lib/estimator"), { virtual: true });
jest.mock("@/lib/waitlistErrors", () => jest.requireActual("../lib/waitlistErrors"), { virtual: true });
jest.mock("@/components/ui/button", () => jest.requireActual("../components/ui/button"), { virtual: true });
jest.mock("@/components/ui/input", () => jest.requireActual("../components/ui/input"), { virtual: true });
jest.mock("@/components/Logo", () => ({ Logo: () => null }), { virtual: true });
jest.mock("@/components/TurnstileWidget", () => {
  const R = require("react");
  return {
    __esModule: true,
    default: function TurnstileStub({ onToken }) {
      R.useEffect(() => { onToken("tok-123"); }, [onToken]);
      return R.createElement("div", { "data-testid": "waitlist-turnstile" });
    },
  };
}, { virtual: true });
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn() },
  errMessage: (e) => String(e),
}), { virtual: true });
jest.mock("sonner", () => ({ toast: { error: jest.fn(), success: jest.fn() } }));

const React = require("react");
const { MemoryRouter } = require("react-router");
const { api } = require("@/lib/api");
const i18n = require("../i18n/estimator").default;
const Estimate = require("./Estimate").default;

// Minimal DOM helpers (React Testing Library isn't a dependency of this repo; adding one is out
// of scope). react-dom/client + act, with native-setter change events for controlled inputs.
const { act } = React;
const { createRoot } = require("react-dom/client");
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;
afterEach(() => {
  act(() => root && root.unmount());
  if (container) container.remove();
  container = root = undefined;
});

const byTestId = (id) => document.querySelector(`[data-testid="${id}"]`);
const getByTestId = (id) => {
  const el = byTestId(id);
  if (!el) throw new Error(`no element with data-testid="${id}"`);
  return el;
};
async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
async function findByTestId(id) {
  for (let i = 0; i < 20; i += 1) {
    const el = byTestId(id);
    if (el) return el;
    await flush();
  }
  return getByTestId(id);
}
function change(el, value) {
  const proto = el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
  act(() => {
    el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  });
}
function submit(form) {
  act(() => { form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
}
const labelFor = (re) => [...document.querySelectorAll("label")].find((l) => re.test(l.textContent));
const text = () => container.textContent;

const PLANS = [
  { key: "trial", name: "Free Trial / Sandbox", price_cents: 0, sort_order: 1, limits: { ai_minutes: 30, calls: 50, sms: 100, locations: 1, users: 1 } },
  { key: "starter", name: "Starter", price_cents: 1999, sort_order: 10, limits: { ai_minutes: 100, calls: 150, sms: 300, locations: 1, users: 2 } },
  { key: "growth", name: "Growth", price_cents: 4999, sort_order: 20, limits: { ai_minutes: 300, calls: 500, sms: 1000, locations: 1, users: 5 } },
  { key: "ai_office", name: "AI Office", price_cents: 9999, sort_order: 30, limits: { ai_minutes: 650, calls: 1500, sms: 3000, locations: 2, users: 10 } },
  { key: "high_volume", name: "High Volume", price_cents: 19999, sort_order: 40, limits: { ai_minutes: 1400, calls: 4000, sms: 8000, locations: 5, users: 25 } },
  { key: "enterprise", name: "Enterprise", price_cents: 0, interval: "custom", sort_order: 50, limits: {} },
];

const ORIGINAL_WAITLIST_ONLY = process.env.REACT_APP_WAITLIST_ONLY;

beforeEach(async () => {
  await i18n.changeLanguage("en");
  api.get.mockReset();
  api.post.mockReset();
  api.get.mockImplementation((url) => Promise.resolve({ data: url === "/plans" ? PLANS : [] }));
  api.post.mockResolvedValue({ data: { ok: true, status: "received" } });
  delete process.env.REACT_APP_WAITLIST_ONLY;
});

afterAll(() => {
  if (ORIGINAL_WAITLIST_ONLY === undefined) delete process.env.REACT_APP_WAITLIST_ONLY;
  else process.env.REACT_APP_WAITLIST_ONLY = ORIGINAL_WAITLIST_ONLY;
});

async function renderPage() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(React.createElement(MemoryRouter, null, React.createElement(Estimate)));
  });
  await findByTestId("estimate-form");
}

function answer(changes = {}) {
  for (const [id, value] of Object.entries(changes)) change(getByTestId(id), value);
  submit(getByTestId("estimate-form"));
}

test("valid answers show the suggested plan with the exact price and pricing links (normal mode)", async () => {
  await renderPage();
  answer({ "estimate-calls": "calls_2" });
  expect((await findByTestId("estimate-plan-name")).textContent).toBe("Growth");
  expect(getByTestId("estimate-plan-price").textContent).toContain("$49.99");
  expect(byTestId("estimate-result-pricing-link")).not.toBeNull();
  expect(byTestId("estimate-header-pricing-link")).not.toBeNull();
});

test("AI minutes question is asked and checked against plan.limits.ai_minutes", async () => {
  await renderPage();
  const label = labelFor(/Minutes of calls answered by AI/);
  expect(label.getAttribute("for")).toBe("est-ai-minutes");
  answer({ "estimate-calls": "calls_1", "estimate-ai-minutes": "ai_4" });
  expect((await findByTestId("estimate-plan-name")).textContent).toBe("High Volume");
  answer({ "estimate-ai-minutes": "ai_unsure" });
  expect((await findByTestId("estimate-plan-name")).textContent).toBe("Starter");
  answer({ "estimate-ai-minutes": "ai_5" });
  expect(getByTestId("estimate-result").textContent).toContain("Above standard limits: AI minutes");
});

test.each([["0"], ["-2"], [""], ["2.5"]])("users=%p shows a validation message instead of a plan", async (value) => {
  await renderPage();
  answer({ "estimate-users": value });
  const err = await findByTestId("estimate-users-error");
  expect(err.textContent).toBe(value === "" ? "Please enter a number." : "Please enter a whole number of 1 or more.");
  expect(getByTestId("estimate-users").getAttribute("aria-invalid")).toBe("true");
  expect(getByTestId("estimate-users").getAttribute("aria-describedby")).toBe("est-users-error");
  expect(byTestId("estimate-result")).toBeNull();
  expect(text()).not.toContain("Starter");
});

test("locations=0 is rejected too, then fixing it shows the plan", async () => {
  await renderPage();
  answer({ "estimate-locations": "0" });
  await findByTestId("estimate-locations-error");
  answer({ "estimate-locations": "1" });
  expect((await findByTestId("estimate-plan-name")).textContent).toBe("Starter");
  expect(byTestId("estimate-locations-error")).toBeNull();
});

// EMP-WL-064 / WL-071
const httpError = (status, detail) => Object.assign(new Error(`Request failed with status code ${status}`), {
  response: { status, data: { detail } },
});
test.each([
  ["en", 422, [{ loc: ["email"], msg: "value is not a valid email address: An email address must have an @-sign." }], "Please enter a valid email address."],
  ["es", 422, [{ loc: ["email"], msg: "value is not a valid email address: An email address must have an @-sign." }], "Ingrese un correo electrónico válido."],
  ["es", 503, "Sorry, we couldn't save your signup right now. Please try again in a few minutes.", "Lo sentimos, no pudimos guardar su registro en este momento. Inténtelo de nuevo en unos minutos."],
])("waitlist error from the estimator (%s, %d) is plain and translated", async (lang, status, detail, expected) => {
  const { toast } = require("sonner");
  api.post.mockImplementationOnce(() => Promise.reject(httpError(status, detail)));
  await renderPage();
  await act(async () => { await i18n.changeLanguage(lang); });
  answer({});
  await findByTestId("estimate-result");
  change(getByTestId("estimate-email"), "owner@example.com");
  submit(getByTestId("estimate-email-form"));
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  expect(toast.error).toHaveBeenLastCalledWith(expected);
});

test("email capture is an honest 'Join the waitlist' with honeypot, Turnstile token and a consent slot", async () => {
  await renderPage();
  answer({ "estimate-calls": "calls_3" });
  const result = await findByTestId("estimate-result");
  expect(result.textContent).toContain("Join the waitlist (optional)");
  expect(getByTestId("estimate-email-submit").textContent).toBe("Join the waitlist");
  expect(text()).not.toMatch(/Email me/i);
  expect(byTestId("estimate-consent-slot")).not.toBeNull();
  const hp = getByTestId("estimate-honeypot");
  expect(hp.getAttribute("name")).toBe("website");
  expect(hp.getAttribute("tabindex")).toBe("-1");
  expect(hp.closest("[aria-hidden='true']")).not.toBeNull();
  expect(byTestId("waitlist-turnstile")).not.toBeNull();

  change(getByTestId("estimate-email"), "owner@example.com");
  submit(getByTestId("estimate-email-form"));
  expect((await findByTestId("estimate-email-sent")).textContent).toContain("You're on the waitlist");
  expect(api.post).toHaveBeenCalledTimes(1);
  const [url, body] = api.post.mock.calls[0];
  expect(url).toBe("/public/waitlist");
  expect(body).toMatchObject({
    email: "owner@example.com", source_detail: "estimator", estimated_tier: "ai_office",
    website: "", turnstile_token: "tok-123",
  });
  expect(body.note).toMatch(/ai=ai_1/);
  expect(body).not.toHaveProperty("phone");
});

test("a filled honeypot is sent as-is (the backend silently drops it)", async () => {
  await renderPage();
  answer({});
  await findByTestId("estimate-result");
  change(getByTestId("estimate-honeypot"), "http://spam.example");
  change(getByTestId("estimate-email"), "bot@example.com");
  submit(getByTestId("estimate-email-form"));
  await findByTestId("estimate-email-sent");
  expect(api.post.mock.calls[0][1].website).toBe("http://spam.example");
});

test("waitlist-only mode: no price, no pricing/trial links, planned-pricing slot shown", async () => {
  process.env.REACT_APP_WAITLIST_ONLY = "true";
  await renderPage();
  answer({ "estimate-calls": "calls_3" });
  expect((await findByTestId("estimate-plan-name")).textContent).toBe("AI Office");
  expect(byTestId("estimate-planned-pricing-slot")).not.toBeNull();
  expect(byTestId("estimate-plan-price")).toBeNull();
  expect(text()).not.toMatch(/\$\d/);
  expect(byTestId("estimate-result-pricing-link")).toBeNull();
  expect(byTestId("estimate-header-pricing-link")).toBeNull();
  expect(container.querySelector('a[href="/pricing"]')).toBeNull();
  expect(container.querySelector('a[href*="signup"], a[href*="trial"], a[href*="login"]')).toBeNull();
  expect(getByTestId("estimate-email-submit").textContent).toBe("Join the waitlist");
});

test("Spanish strings exist for the new labels", async () => {
  await i18n.changeLanguage("es");
  await renderPage();
  expect(labelFor(/Minutos de llamadas atendidas por IA/)).toBeTruthy();
  answer({ "estimate-users": "0" });
  expect((await findByTestId("estimate-users-error")).textContent).toBe("Ingrese un número entero de 1 o más.");
  answer({ "estimate-users": "1" });
  await findByTestId("estimate-result");
  expect(getByTestId("estimate-email-submit").textContent).toBe("Unirse a la lista de espera");
});
