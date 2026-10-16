// LeadAssistant (Mae): conversational lead capture on the landing page. The chat must feed the
// SAME lead path as the page form — POST /public/waitlist with the form's payload shape — and
// dismissal must persist. Timers are fake; Mae's "thinking" delay is 550 ms per message.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });

jest.mock("@/lib/api", () => ({
  api: { get: jest.fn(() => Promise.resolve({ data: [] })), post: jest.fn() },
  errMessage: (e) => String((e && e.message) || e),
}), { virtual: true });
jest.mock("@/components/TurnstileWidget", () => () => null, { virtual: true });

const React = require("react");
require("../i18n");
const { api } = require("@/lib/api");
const LeadAssistant = require("./LeadAssistant").default;
const { act } = React;
const { createRoot } = require("react-dom/client");
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;
beforeEach(() => {
  jest.useFakeTimers();
  window.localStorage.clear();
  api.post.mockReset();
  api.post.mockResolvedValue({ data: {} });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.useRealTimers();
});

const q = (id) => document.querySelector(`[data-testid="${id}"]`);
const tick = (ms) => act(() => { jest.advanceTimersByTime(ms); });
const flushMicro = async () => { await act(async () => { await Promise.resolve(); }); };
const chipByText = (text) => [...document.querySelectorAll(".mae-chip")].find((b) => b.textContent === text);
const bodyText = () => container.textContent;

function render(props = {}) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(React.createElement(LeadAssistant, props)));
}

function typeAndSend(text) {
  const input = q("mae-input");
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  act(() => {
    setter.call(input, text);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
  const form = input.closest("form");
  act(() => form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true })));
}

// Walk the whole scripted flow with chips + two typed answers, landing on the contact step.
async function walkToContact() {
  render();
  act(() => q("mae-fab").click());
  tick(1200); // hello + trade question
  act(() => chipByText("HVAC").click());
  tick(1200); // ack + size question
  act(() => chipByText("2–5").click());
  tick(1200); // ack + headache question
  act(() => chipByText("Missed calls").click());
  tick(1200); // fix line + name question
  typeAndSend("Sam");
  tick(1200); // ack + business question
  act(() => chipByText("Skip").click());
  tick(1200); // contact question
}

test("opens, greets, and asks the trade question first", async () => {
  render();
  expect(q("mae-fab")).not.toBeNull();
  act(() => q("mae-fab").click());
  tick(1200);
  expect(bodyText()).toMatch(/I'm Mae/);
  expect(bodyText()).toMatch(/what kind of work do you do/i);
  expect(chipByText("HVAC")).toBeTruthy();
  expect(chipByText("Just browsing — show me pricing")).toBeTruthy();
}, 15000);

test("full flow submits to the same lead endpoint with the form's fields", async () => {
  await walkToContact();
  typeAndSend("sam@example.com");
  await flushMicro();
  tick(100);
  expect(api.post).toHaveBeenCalledTimes(1);
  const [path, payload] = api.post.mock.calls[0];
  expect(path).toBe("/public/waitlist");
  expect(payload).toMatchObject({
    email: "sam@example.com",
    name: "Sam",
    business_name: "",
    industry: "HVAC",
    website: "",
  });
  expect(payload.note).toContain("trade=HVAC");
  expect(payload.note).toContain("crew=2-5");
  expect(payload.note).toContain("headache=missed");
  expect(bodyText()).toMatch(/You're on the list\./);
  expect(bodyText()).toMatch(/what happens next/i);
}, 20000);

test("phone-only contact asks for an email too, then submits with the phone in the note", async () => {
  await walkToContact();
  typeAndSend("555-123-4567");
  tick(1200);
  expect(api.post).not.toHaveBeenCalled();
  expect(bodyText()).toMatch(/runs on email/i);
  typeAndSend("sam@example.com");
  await flushMicro();
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post.mock.calls[0][1].note).toContain("phone=555-123-4567");
}, 20000);

test("endpoint failure offers the regular form as a fallback", async () => {
  const onShowForm = jest.fn();
  api.post.mockRejectedValue({ response: { status: 503 } });
  render({ onShowForm });
  act(() => q("mae-fab").click());
  tick(1200);
  act(() => chipByText("HVAC").click());
  tick(1200);
  act(() => chipByText("2–5").click());
  tick(1200);
  act(() => chipByText("Missed calls").click());
  tick(1200);
  typeAndSend("Sam");
  tick(1200);
  act(() => chipByText("Skip").click());
  tick(1200);
  typeAndSend("sam@example.com");
  await flushMicro();
  tick(100);
  expect(bodyText()).toMatch(/That didn't go through\./);
  const formChip = chipByText("Use the regular form instead");
  expect(formChip).toBeTruthy();
  act(() => formChip.click());
  expect(onShowForm).toHaveBeenCalledTimes(1);
}, 20000);

test("dismissal hides the mascot and is remembered in localStorage", async () => {
  render();
  act(() => q("mae-dismiss").click());
  expect(q("lead-assistant")).toBeNull();
  expect(window.localStorage.getItem("mae.dismissed.v1")).toBeTruthy();
  // A fresh mount (new page view) stays dismissed.
  act(() => root.unmount());
  container.remove();
  render();
  expect(q("lead-assistant")).toBeNull();
}, 15000);

test("just browsing hands off to pricing instead of capturing", async () => {
  const onShowPricing = jest.fn();
  render({ onShowPricing });
  act(() => q("mae-fab").click());
  tick(1200);
  act(() => chipByText("Just browsing — show me pricing").click());
  expect(onShowPricing).toHaveBeenCalledTimes(1);
  expect(api.post).not.toHaveBeenCalled();
}, 15000);
