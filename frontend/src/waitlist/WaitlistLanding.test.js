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
    // EMP-WL-016: no EN/ES switcher in the waitlist build (the page is English-only there).
    expect(q('[data-testid="language-switcher-btn"]')).toBeNull();
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

describe("waitlist landing UI (EMP-WL-006 / 034 / 016 / 001)", () => {
  beforeEach(() => { process.env.REACT_APP_WAITLIST_ONLY = "true"; });

  test("header and hero have a 'Join waitlist' CTA that targets the form and focuses the email field", async () => {
    await renderAt("/");
    for (const id of ["header-cta-waitlist", "hero-cta-waitlist"]) {
      const cta = q(`[data-testid="${id}"]`);
      expect(cta).not.toBeNull();
      expect(cta.getAttribute("href")).toBe("#waitlist"); // works without JS too
      expect(cta.textContent).toBe("Join waitlist"); // existing locale label (landing.cta_join_waitlist)
      const section = q("#waitlist");
      section.scrollIntoView = jest.fn();
      act(() => { document.body.focus(); });
      const evt = new MouseEvent("click", { bubbles: true, cancelable: true });
      act(() => { cta.dispatchEvent(evt); });
      expect(evt.defaultPrevented).toBe(true);
      expect(section.scrollIntoView).toHaveBeenCalledTimes(1);
      expect(document.activeElement).toBe(q('[data-testid="waitlist-email"]'));
      expect(section.contains(q('[data-testid="waitlist-form"]'))).toBe(true);
    }
  });

  test("a Spanish browser/saved choice still gets one language (English) on the waitlist page", async () => {
    const i18n = require("@/i18n").default;
    await act(async () => { await i18n.changeLanguage("es"); });
    try {
      await renderAt("/");
      expect(q('[data-testid="header-cta-waitlist"]').textContent).toBe("Join waitlist");
      expect(q('[data-testid="waitlist-submit"]').textContent).toBe("Join waitlist");
      expect(q('label[for="wl-email"]').textContent).toBe("Email"); // EMP-WL-013: visible label replaces aria-label
    } finally {
      await act(async () => { await i18n.changeLanguage("en"); });
    }
  });

  test("Join is the primary button and is not greyed out before an email is typed", async () => {
    await renderAt("/");
    const submit = q('[data-testid="waitlist-submit"]');
    expect(submit.disabled).toBe(false);
    expect(submit.className).toMatch(/bg-white/);
    expect(submit.className).toMatch(/text-black/);
    expect(q('[data-testid="waitlist-email"]').required).toBe(true); // empty submit still blocked
  });

  test("no Log in / Sign up / trial copy and no language switcher anywhere on the page", async () => {
    await renderAt("/");
    const text = container.textContent;
    expect(text).not.toMatch(/log ?in|sign ?up|trial|try it free|not ready to try|start free|create your workspace/i);
    expect(q('[data-testid="language-switcher-btn"]')).toBeNull();
    expect(q("#how")).toBeNull();
    expect(q('a[href="#how"]')).toBeNull();
  });

  test("layout classes: card and hero are one column on phones (no 12-column grid below md/lg)", async () => {
    await renderAt("/");
    const card = q("#waitlist > div");
    expect(card.className).toMatch(/\bgrid-cols-1\b/);
    expect(card.className).toMatch(/\bmd:grid-cols-12\b/);
    expect(card.className).not.toMatch(/(^|\s)grid-cols-12\b/);
    expect(card.className).toMatch(/\bp-5\b/);
    const nameRow = q('[data-testid="waitlist-name"]').parentElement.parentElement; // field wrapper (label + input), then the row
    expect(nameRow.className).toMatch(/\bgrid-cols-1\b/);
    expect(nameRow.className).toMatch(/\bsm:grid-cols-2\b/);
    // No unprefixed 12-column grid anywhere in the waitlist page.
    const twelve = [...container.querySelectorAll("[class]")].filter((el) => /(^|\s)grid-cols-12(\s|$)/.test(el.className));
    expect(twelve).toEqual([]);
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
    // Full build keeps the switcher, How it works and the trial card line; no waitlist CTAs.
    expect(q('[data-testid="language-switcher-btn"]')).not.toBeNull();
    expect(q("#how")).not.toBeNull();
    expect(container.textContent).toMatch(/Not ready to try\?/);
    expect(q('[data-testid="header-cta-waitlist"]')).toBeNull();
    expect(q('[data-testid="hero-cta-waitlist"]')).toBeNull();
  });
});
