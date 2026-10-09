// EMP-WL-026 / WL-027: render the REAL Landing page (and the real LanguageSwitcher) inside the
// waitlist-only router, with NO AuthProvider, exactly like the waitlist build. Only the network
// (api) is mocked. Before the fix this page was blank: LanguageSwitcher destructured useAuth().
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
  errMessage: (e) => String(e),
}));

const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter } = require("react-router");
require("@/i18n");
const { api } = require("@/lib/api");
const { WaitlistRoutes } = require("./WaitlistApp");

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const ORIGINAL = process.env.REACT_APP_WAITLIST_ONLY;

let container;
let root;
let errors;
beforeEach(() => {
  api.get.mockImplementation(() => Promise.resolve({ data: [] }));
  api.post.mockImplementation(() => Promise.resolve({ data: { ok: true } }));
  errors = jest.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  errors.mockRestore();
  if (ORIGINAL === undefined) delete process.env.REACT_APP_WAITLIST_ONLY;
  else process.env.REACT_APP_WAITLIST_ONLY = ORIGINAL;
});

async function renderAt(path) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <WaitlistRoutes />
      </MemoryRouter>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const q = (sel) => container.querySelector(sel);

describe("waitlist-only build (REACT_APP_WAITLIST_ONLY=true)", () => {
  beforeEach(() => { process.env.REACT_APP_WAITLIST_ONLY = "true"; });

  test("home renders the real Landing with the waitlist form, without an AuthProvider", async () => {
    await renderAt("/");
    expect(q('[data-testid="landing-page"]')).not.toBeNull();
    expect(q('[data-testid="waitlist-form"]')).not.toBeNull();
    expect(q('[data-testid="waitlist-email"]')).not.toBeNull();
    expect(q('[data-testid="waitlist-submit"]')).not.toBeNull();
    expect(q('[data-testid="language-switcher-btn"]')).not.toBeNull(); // real switcher, no auth
    const crashes = errors.mock.calls.map((c) => String(c[0])).filter((m) => /Cannot destructure|uncaught|The above error/i.test(m));
    expect(crashes).toEqual([]);
  });

  test("no demo, trial, login or pricing buttons/links", async () => {
    await renderAt("/");
    for (const id of ["landing-login-btn", "landing-signup-btn", "hero-cta-signup", "hero-cta-demo",
      "final-cta-signup", "hero-trial-copy", "demo-start"]) {
      expect(q(`[data-testid="${id}"]`)).toBeNull();
    }
    expect(q("#demo")).toBeNull();
    for (const href of ["/login", "/signup", "/pricing", "#demo"]) {
      expect(q(`a[href="${href}"]`)).toBeNull();
    }
    expect(container.textContent).not.toMatch(/Try it free|Start demo call|Try the live demo/);
    // Privacy/Terms links stay.
    expect(q('a[href="/privacy"]')).not.toBeNull();
    expect(q('a[href="/terms"]')).not.toBeNull();
  });

  test("makes no /api/plans (or any GET) call", async () => {
    await renderAt("/");
    expect(api.get).not.toHaveBeenCalled();
  });

  test("the waitlist form still posts", async () => {
    await renderAt("/");
    const email = q('[data-testid="waitlist-email"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    act(() => {
      setter.call(email, "owner@example.com");
      email.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      q('[data-testid="waitlist-form"]').dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(api.post).toHaveBeenCalledWith("/public/waitlist", expect.objectContaining({ email: "owner@example.com" }));
    expect(q('[data-testid="waitlist-success"]')).not.toBeNull();
  });

  test("privacy and terms render the real pages", async () => {
    await renderAt("/privacy");
    expect(container.textContent).toMatch(/privacy/i);
    act(() => root.unmount());
    container.remove();
    await renderAt("/terms");
    expect(container.textContent).toMatch(/terms/i);
  });
});

describe("normal build (flag off): Landing unchanged", () => {
  beforeEach(() => { delete process.env.REACT_APP_WAITLIST_ONLY; });

  test("login, trial, pricing and demo are still there and /api/plans is fetched", async () => {
    await renderAt("/");
    for (const id of ["landing-login-btn", "landing-signup-btn", "hero-cta-signup", "hero-cta-demo", "final-cta-signup"]) {
      expect(q(`[data-testid="${id}"]`)).not.toBeNull();
    }
    expect(q("#demo")).not.toBeNull();
    expect(q('a[href="/pricing"]')).not.toBeNull();
    expect(api.get).toHaveBeenCalledWith("/plans");
  });
});
