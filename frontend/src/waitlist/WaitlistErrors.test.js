// EMP-WL-064 / WL-071: waitlist signup error text (translated 503, plain validation message).
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("react-router-dom", () => jest.requireActual("react-router"));
jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(), post: jest.fn(), put: jest.fn() },
  errMessage: (e) => (e && e.message) || String(e),
}));

const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter } = require("react-router");
const { api } = require("@/lib/api");
const { WaitlistRoutes } = require("./WaitlistApp");
const i18n = require("@/i18n").default;
const { waitlistErrorText } = require("@/lib/waitlistErrors");

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
  act(() => { i18n.changeLanguage("en"); });
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


const httpError = (status, detail) => Object.assign(new Error(`Request failed with status code ${status}`), {
  response: { status, data: { detail } },
});
const BACKEND_503 = "Sorry, we couldn't save your signup right now. Please try again in a few minutes.";
const PYDANTIC_422 = [{ type: "value_error", loc: ["email"], msg: "value is not a valid email address: An email address must have an @-sign.", ctx: { reason: "An email address must have an @-sign." } }];

async function failWith(err, lang) {
  api.post.mockImplementationOnce(() => Promise.reject(err));
  await renderHome();
  if (lang) await act(async () => { await i18n.changeLanguage(lang); });
  type(q('[data-testid="waitlist-email"]'), "owner@example.com");
  await submit();
  return q('[data-testid="waitlist-error"]').textContent;
}

describe("normal build", () => {
  beforeEach(() => { delete process.env.REACT_APP_WAITLIST_ONLY; });

  test("WL-064: the 503 message is Spanish in the Spanish build", async () => {
    const text = await failWith(httpError(503, BACKEND_503), "es");
    expect(text).toBe("Lo sentimos, no pudimos guardar su registro en este momento. Inténtelo de nuevo en unos minutos.");
  });

  test("WL-064: and English in English", async () => {
    expect(await failWith(httpError(503, BACKEND_503), "en")).toBe(BACKEND_503);
  });

  test("WL-071: an invalid email shows a plain message, never pydantic's text (es)", async () => {
    const text = await failWith(httpError(422, PYDANTIC_422), "es");
    expect(text).toBe("Ingrese un correo electrónico válido.");
  });
});

describe("waitlist-only build (English-only page, EMP-WL-016)", () => {
  beforeEach(() => { process.env.REACT_APP_WAITLIST_ONLY = "true"; });

  test("WL-071: invalid email -> plain English message", async () => {
    const text = await failWith(httpError(422, PYDANTIC_422));
    expect(text).toBe("Please enter a valid email address.");
    expect(text).not.toMatch(/value is not a valid|@-sign/);
  });

  test("WL-064: 503 stays English even with Spanish selected", async () => {
    expect(await failWith(httpError(503, BACKEND_503), "es")).toBe(BACKEND_503);
  });
});

describe("waitlistErrorText", () => {
  const t = (k) => `T:${k}`;
  const fallback = (e) => `F:${e.message}`;
  test("other 422 fields get the generic form message", () => {
    expect(waitlistErrorText(httpError(422, [{ loc: ["note"], msg: "String should have at most 2000 characters" }]), t, fallback))
      .toBe("T:waitlistErrors.invalid");
  });
  test("429 / 400 / network errors keep the existing text", () => {
    expect(waitlistErrorText(httpError(429, "Too many requests."), t, fallback)).toBe("F:Request failed with status code 429");
    expect(waitlistErrorText(new Error("Network Error"), t, fallback)).toBe("F:Network Error");
  });
  test("every string exists in en and es, and the shared locale files are untouched", () => {
    const { WAITLIST_ERROR_STRINGS } = require("@/lib/waitlistErrors");
    for (const k of ["unavailable", "email", "invalid"]) {
      expect(WAITLIST_ERROR_STRINGS.en.waitlistErrors[k]).toBeTruthy();
      expect(WAITLIST_ERROR_STRINGS.es.waitlistErrors[k]).toBeTruthy();
    }
    const fs = require("fs");
    const path = require("path");
    for (const lang of ["en", "es"]) {  // read from disk: i18next merges bundles into the loaded object
      const file = fs.readFileSync(path.join(__dirname, "..", "i18n", "locales", `${lang}.json`), "utf8");
      expect(file).not.toMatch(/waitlistErrors|waitlist_error/);
    }
  });
});
