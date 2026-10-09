// EMP-WL-013: waitlist form labels, linked inline errors and text/button contrast.
// jest-axe is not installed; axe-core is (transitively), so it is run directly. jsdom has no
// layout, so axe's color-contrast rule cannot run here: contrast is covered by
// scripts/waitlist-contrast.js (same numbers used below) plus a class scan of the rendered page.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
  errMessage: (e) => (e && e.message) || String(e),
}));

const fs = require("fs");
const path = require("path");
const axe = require("axe-core");
const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter } = require("react-router");
require("@/i18n");
const { api } = require("@/lib/api");
const { WaitlistRoutes } = require("./WaitlistApp");
const contrastScript = require("../../scripts/waitlist-contrast");

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

async function renderHome() {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/"]}>
        <WaitlistRoutes />
      </MemoryRouter>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const q = (sel) => container.querySelector(sel);

function type(el, value) {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  act(() => {
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}
async function submit() {
  await act(async () => {
    q('[data-testid="waitlist-form"]').dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function axeViolations(node) {
  const res = await axe.run(node, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
    rules: { "color-contrast": { enabled: false } }, // needs real layout; see contrast tests below
  });
  return res.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

const FIELDS = [
  ["waitlist-email", "Email"],
  ["waitlist-name", "Your name"],
  ["waitlist-biz", "Business"],
  ["waitlist-industry", "Industry"],
  ["waitlist-note", "Anything specific you'd want it to do?"],
];

describe.each([["waitlist-only build", "true"], ["normal build", undefined]])("%s", (_name, flag) => {
  beforeEach(() => {
    if (flag === undefined) delete process.env.REACT_APP_WAITLIST_ONLY;
    else process.env.REACT_APP_WAITLIST_ONLY = flag;
  });

  test("every waitlist field has a visible <label for> naming it", async () => {
    await renderHome();
    for (const [testid, text] of FIELDS) {
      const field = q(`[data-testid="${testid}"]`);
      expect(field.id).toBeTruthy();
      const label = q(`label[for="${field.id}"]`);
      expect(label).not.toBeNull();
      expect(label.textContent).toBe(text);
      expect(field.labels[0]).toBe(label);
    }
    expect(q('[data-testid="waitlist-email"]').getAttribute("autocomplete")).toBe("email");
  });

  test("axe (WCAG 2.1 A/AA) finds no violations in the waitlist section", async () => {
    await renderHome();
    expect(await axeViolations(q("#waitlist"))).toEqual([]);
  });
});

describe("waitlist errors and success (waitlist-only build)", () => {
  beforeEach(() => { process.env.REACT_APP_WAITLIST_ONLY = "true"; });

  test("a failed signup shows an inline alert linked to the email field, and clears on retry", async () => {
    api.post.mockImplementationOnce(() => Promise.reject(new Error("Sorry, we couldn't save your signup right now.")));
    await renderHome();
    const email = q('[data-testid="waitlist-email"]');
    expect(email.getAttribute("aria-invalid")).toBeNull();
    expect(email.getAttribute("aria-describedby")).toBeNull();
    type(email, "owner@example.com");
    await submit();
    const err = q('[data-testid="waitlist-error"]');
    expect(err).not.toBeNull();
    expect(err.getAttribute("role")).toBe("alert");
    expect(err.textContent).toMatch(/couldn't save your signup/);
    expect(email.getAttribute("aria-invalid")).toBe("true");
    expect(email.getAttribute("aria-describedby")).toBe(err.id);
    expect(document.getElementById(email.getAttribute("aria-describedby"))).toBe(err);
    expect(await axeViolations(q("#waitlist"))).toEqual([]);

    await submit(); // second attempt succeeds
    expect(q('[data-testid="waitlist-error"]')).toBeNull();
    const ok = q('[data-testid="waitlist-success"]');
    expect(ok).not.toBeNull();
    expect(ok.getAttribute("role")).toBe("status");
    expect(document.activeElement).toBe(ok); // focus moves to the confirmation
  });
});

describe("Join waitlist CTAs (EMP-WL-051)", () => {
  beforeEach(() => { process.env.REACT_APP_WAITLIST_ONLY = "true"; });

  test("each CTA is ONE interactive element: a styled link, no <button> inside an <a>", async () => {
    await renderHome();
    for (const id of ["header-cta-waitlist", "hero-cta-waitlist"]) {
      const cta = q(`[data-testid="${id}"]`);
      expect(cta.tagName).toBe("A");
      expect(cta.getAttribute("href")).toBe("#waitlist");
      expect(cta.querySelector("button, a, input, [tabindex]")).toBeNull();
      expect(cta.closest("button")).toBeNull();
      expect(cta.className).toMatch(/bg-white/); // still looks like the primary button
      expect(cta.className).toMatch(/text-black/);
      expect(cta.textContent).toBe("Join waitlist");
    }
    // Nowhere on the page: interactive content nested in a link or button.
    expect(container.querySelectorAll("a button, a a, button a, button button")).toHaveLength(0);
  });

  test("axe finds no violations on the whole waitlist page (incl. nested-interactive)", async () => {
    await renderHome();
    expect(await axeViolations(container)).toEqual([]);
  });
});

describe("contrast (WCAG AA 4.5:1 text, 3:1 input borders)", () => {
  test("contrast math matches known WCAG values", () => {
    const { contrast } = contrastScript;
    expect(contrast([0, 0, 0], [255, 255, 255])).toBeCloseTo(21, 5);
    expect(contrast([255, 255, 255], [255, 255, 255])).toBeCloseTo(1, 5);
    expect(contrast([118, 118, 118], [255, 255, 255])).toBeCloseTo(4.54, 2); // #767676 on white
  });

  test("every color pair used on the waitlist page meets its minimum", () => {
    const failing = contrastScript.results().filter((r) => r.ratio < r.min);
    expect(failing).toEqual([]);
  });

  test("the pre-fix colors really failed (guards against a meaningless check)", () => {
    const before = Object.fromEntries(contrastScript.BEFORE.map((r) => [r.name, r.ratio]));
    expect(before["text-white/40 on page (old step numbers, ©)"]).toBeLessThan(4.5);
    expect(before["old .overline hsl(220 10% 44%) on page"]).toBeLessThan(4.5);
  });

  test.each([["true"], [undefined]])("rendered page uses no white text below /60 (flag=%s)", async (flag) => {
    if (flag === undefined) delete process.env.REACT_APP_WAITLIST_ONLY;
    else process.env.REACT_APP_WAITLIST_ONLY = flag;
    await renderHome();
    const low = [];
    for (const el of container.querySelectorAll("[class]")) {
      for (const m of String(el.className).matchAll(/(?:^|\s)(?:[a-z]+:)?(?:text|placeholder:text)-white\/(\d+)/g)) {
        if (Number(m[1]) < 60) low.push(`${el.tagName.toLowerCase()} ${m[0].trim()}`);
      }
    }
    expect(low).toEqual([]);
    // The overline color comes from plain CSS that wins over text-white/* utilities.
    const overlines = container.querySelectorAll(".overline");
    expect(overlines.length).toBeGreaterThan(0);
  });

  test("index.css overrides the light-theme overline color on the dark marketing pages", () => {
    const css = fs.readFileSync(path.join(__dirname, "..", "index.css"), "utf8");
    const rule = css.match(/\.marketing-shell\s+\.overline\s*\{([^}]*)\}/);
    expect(rule).not.toBeNull();
    const m = rule[1].match(/rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*([0-9.]+)\s*\)/);
    expect(m).not.toBeNull();
    expect(Number(m[1])).toBeGreaterThanOrEqual(0.6);
    expect(css.indexOf(".marketing-shell .overline")).toBeGreaterThan(css.indexOf(".overline {"));
  });
});
