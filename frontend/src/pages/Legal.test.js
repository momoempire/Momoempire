// EMP-WL-053: in the waitlist-only build /privacy and /terms must not describe a trial, pricing,
// plans or billing. Those sentences are hidden behind TODO(Brann/counsel) slots (legal text is
// not rewritten); the full build is unchanged. Renders the real pages via the waitlist routes.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(() => Promise.resolve({ data: [] })), post: jest.fn(), put: jest.fn() },
  errMessage: (e) => String(e),
}));

const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter } = require("react-router");
require("@/i18n");
const { WaitlistRoutes } = require("@/waitlist/WaitlistApp");
const { Privacy, Terms } = require("./Legal");

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const ORIGINAL = process.env.REACT_APP_WAITLIST_ONLY;
let container;
let root;
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  if (ORIGINAL === undefined) delete process.env.REACT_APP_WAITLIST_ONLY;
  else process.env.REACT_APP_WAITLIST_ONLY = ORIGINAL;
});

async function render(element, path = "/") {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(<MemoryRouter initialEntries={[path]}>{element}</MemoryRouter>);
  });
}

// Words that only make sense with a trial / paid plans. "Stripe (payments)" in the vendor list
// and "fees you paid us" in Liability are deliberately left for counsel (see PR).
const FORBIDDEN = /trial|pricing|credit card|billing|\bbill\b|billed|subscription|\bplans?\b|plan's|overage|invoice|auto-renew/i;

describe("waitlist-only build", () => {
  beforeEach(() => { process.env.REACT_APP_WAITLIST_ONLY = "true"; });

  test.each([["/privacy", "privacy-workspace-content"], ["/terms", "terms-billing-trial"]])(
    "%s has no trial/pricing/billing text and shows TODO(Brann/counsel) slots", async (path, slot) => {
      await render(<WaitlistRoutes />, path);
      const main = container.querySelector("main");
      expect(main).not.toBeNull();
      const text = main.textContent;
      expect(text).not.toMatch(FORBIDDEN);
      expect(text).not.toMatch(/No credit card is required/);
      const slots = [...container.querySelectorAll('[data-testid="legal-todo-slot"]')];
      expect(slots.length).toBeGreaterThan(0);
      expect(slots.map((s) => s.dataset.todoSlot)).toContain(slot);
      for (const s of slots) expect(s.textContent).toMatch(/^\[TODO\(Brann\/counsel\): /);
    });

  test("the rest of the legal text is untouched (not rewritten)", async () => {
    await render(<Terms />);
    const t = container.textContent;
    expect(t).toMatch(/You must be 18\+ and the authorized operator/);
    expect(t).toMatch(/Service is provided "as is"/);
    act(() => root.unmount()); container.remove();
    await render(<Privacy />);
    expect(container.textContent).toMatch(/We do not sell your data\./);
    expect(container.textContent).toMatch(/Account data/);
  });
});

describe("full build (flag off): unchanged", () => {
  beforeEach(() => { delete process.env.REACT_APP_WAITLIST_ONLY; });

  test("terms keep Billing, Trial and Service levels text; no TODO slots", async () => {
    await render(<Terms />);
    const t = container.textContent;
    expect(t).toMatch(/Monthly plans auto-renew\./);
    expect(t).toMatch(/The free trial has limits shown live on the pricing page\. No credit card is required\./);
    expect(t).toMatch(/no formal SLA on free\/starter plans/);
    expect(container.querySelector('[data-testid="legal-todo-slot"]')).toBeNull();
    expect([...container.querySelectorAll("h2")].map((h) => h.textContent)).toEqual(
      ["Account", "Acceptable use", "Billing", "Trial", "AI content", "Service levels", "Termination", "Liability", "Changes", "Contact"]);
  });

  test("privacy keeps payment, usage, billing and retention text; no TODO slots", async () => {
    await render(<Privacy />);
    const t = container.textContent;
    expect(t).toMatch(/We only store subscription IDs and plan state/);
    expect(t).toMatch(/basic logs for billing \+ abuse prevention/);
    expect(t).toMatch(/To bill the right amount/);
    expect(t).toMatch(/90 days after cancellation for billing reconciliation/);
    expect(t).toMatch(/services, pricing, knowledge/);
    expect(container.querySelectorAll("li").length).toBe(9);
    expect(container.querySelector('[data-testid="legal-todo-slot"]')).toBeNull();
  });
});

// EMP-WL-082: "Last updated" is a fixed build-time value, never today's date.
describe("Last updated date", () => {
  const ORIGINAL_DATE = process.env.REACT_APP_LEGAL_LAST_UPDATED;
  afterEach(() => {
    if (ORIGINAL_DATE === undefined) delete process.env.REACT_APP_LEGAL_LAST_UPDATED;
    else process.env.REACT_APP_LEGAL_LAST_UPDATED = ORIGINAL_DATE;
    jest.useRealTimers();
  });

  test.each([["privacy", Privacy], ["terms", Terms]])("%s shows the configured date exactly", async (_n, Page) => {
    process.env.REACT_APP_LEGAL_LAST_UPDATED = "October 1, 2026";
    await render(<Page />);
    expect(container.querySelector('[data-testid="legal-last-updated"]').textContent).toBe("Last updated: October 1, 2026");
    expect(container.querySelector('[data-testid="legal-last-updated-todo"]')).toBeNull();
  });

  test.each([["privacy", Privacy], ["terms", Terms]])("%s without a date shows a TODO(Brann) slot, not today", async (_n, Page) => {
    delete process.env.REACT_APP_LEGAL_LAST_UPDATED;
    jest.useFakeTimers({ now: new Date("2031-02-03T12:00:00Z") });
    await render(<Page />);
    const line = container.querySelector('[data-testid="legal-last-updated"]');
    expect(line.textContent).toBe("Last updated: [TODO(Brann): real date]");
    expect(line.textContent).not.toMatch(/2031|\d{1,2}\/\d{1,2}\/\d{2,4}/);
  });

  test("the date does not change with the clock", async () => {
    process.env.REACT_APP_LEGAL_LAST_UPDATED = "2026-10-01";
    jest.useFakeTimers({ now: new Date("2030-01-01T00:00:00Z") });
    await render(<Privacy />);
    expect(container.querySelector('[data-testid="legal-last-updated"]').textContent).toBe("Last updated: 2026-10-01");
  });
});
