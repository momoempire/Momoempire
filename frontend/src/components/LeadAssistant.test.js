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

/* ---------------- v2: full-body stage, locomotion, come-alive, skins ---------------- */

const stage = () => q("mae-stage");
const poseNow = () => stage() && stage().getAttribute("data-pose");
const walkerX = () => {
  const w = container.querySelector(".mae-walker");
  const m = /translate3d\((-?[\d.]+)px/.exec((w && w.style.transform) || "");
  return m ? parseFloat(m[1]) : null;
};

test("patrols the bottom stage: idle → walk (flipping to face travel) → idle, and she moves", () => {
  const rnd = jest.spyOn(Math, "random").mockReturnValue(0.1);
  try {
    render();
    expect(stage()).not.toBeNull();
    expect(poseNow()).toBe("idle");
    const startX = walkerX();
    tick(7000); // intro passes (routine starts ~6.5s); mocked random always picks "walk"
    expect(poseNow()).toBe("walk");
    // she started at the right edge and walks left, so she flips to face the travel direction
    expect(container.querySelector(".mae-flip").style.transform).toBe("scaleX(-1)");
    tick(12000); // ~12s of walking at 62 px/s puts her at her target; she settles back to idle
    expect(poseNow()).toBe("idle");
    expect(walkerX()).not.toBe(startX);
    expect(walkerX()).toBeLessThan(startX);
  } finally {
    rnd.mockRestore();
  }
}, 20000);

test("dance is capped: short, never twice in a row, at most once per ~45s", () => {
  const rnd = jest.spyOn(Math, "random").mockReturnValue(0.99); // always asks for the dance branch
  try {
    render();
    const poses = [];
    for (let i = 0; i < 640; i += 1) {
      tick(250);
      poses.push(poseNow());
    }
    const episodes = [];
    let cur = null;
    poses.forEach((p, i) => {
      if (p === "dance") {
        if (!cur) cur = { start: i, end: i };
        else cur.end = i;
      } else if (cur) {
        episodes.push(cur);
        cur = null;
      }
    });
    if (cur) episodes.push(cur);
    expect(episodes.length).toBeGreaterThanOrEqual(2);
    expect(episodes.length).toBeLessThanOrEqual(5);
    episodes.forEach((e) => {
      expect((e.end - e.start + 1) * 250).toBeLessThanOrEqual(3000); // ~2.3s dances
    });
    for (let i = 1; i < episodes.length; i += 1) {
      // start-to-start gap honours the ~45s cap (with sampling slack)
      expect((episodes[i].start - episodes[i - 1].start) * 250).toBeGreaterThanOrEqual(44000);
    }
    expect(poses).toContain("walk");
    expect(poses).toContain("wave");
  } finally {
    rnd.mockRestore();
  }
}, 60000);

test("clicking Mae brings her alive and she takes her duty spot beside the panel", () => {
  render();
  act(() => q("mae-fab").click());
  expect(poseNow()).toBe("alive");
  expect(document.querySelector(".mae-panel")).not.toBeNull();
  expect(container.querySelector(".mae-bubble").textContent).toMatch(/AI front desk/);
  tick(1200);
  expect(bodyText()).toMatch(/I'm Mae/);
  tick(4500); // hop/spin done, she hurries to the dock and stands on duty
  expect(poseNow()).toBe("duty");
}, 20000);

test("closing the panel sends her back on patrol after a few seconds", () => {
  const rnd = jest.spyOn(Math, "random").mockReturnValue(0.1);
  try {
    render();
    act(() => q("mae-fab").click());
    tick(6000);
    expect(poseNow()).toBe("duty");
    act(() => document.querySelector(".mae-panel-close").click());
    expect(document.querySelector(".mae-panel")).toBeNull();
    tick(6000); // resume delay + routine delay → walking again
    expect(poseNow()).toBe("walk");
  } finally {
    rnd.mockRestore();
  }
}, 20000);

test("dismissal stops her mid-patrol and removes the stage for good", () => {
  const rnd = jest.spyOn(Math, "random").mockReturnValue(0.1);
  try {
    render();
    tick(7000);
    expect(poseNow()).toBe("walk");
    act(() => q("mae-dismiss").click());
    expect(q("lead-assistant")).toBeNull();
    expect(window.localStorage.getItem("mae.dismissed.v1")).toBeTruthy();
    tick(30000); // no ambient motion (or anything else) ever comes back
    expect(q("lead-assistant")).toBeNull();
  } finally {
    rnd.mockRestore();
  }
}, 20000);

test("reduced motion: Mae stands still in the corner, and a click still opens the chat", () => {
  window.matchMedia = (query) => ({
    matches: true,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  });
  try {
    render();
    expect(stage().className).toMatch(/is-static/);
    const x0 = walkerX();
    tick(30000);
    expect(poseNow()).toBe("idle");
    expect(walkerX()).toBe(x0);
    act(() => q("mae-fab").click());
    expect(poseNow()).toBe("duty");
    // no typing delay under reduced motion — the greeting is already there
    expect(bodyText()).toMatch(/I'm Mae/);
  } finally {
    delete window.matchMedia;
  }
}, 20000);

/* ---------------- skins: one rig, tier-gated artwork ---------------- */

const RIG_SELECTORS = [".mae-leg-l", ".mae-leg-r", ".mae-arm-point", ".mae-arm-wave", ".mae-head", ".mae-eyes", ".mae-upper"];

function expectRig(skinId) {
  expect(stage().getAttribute("data-skin")).toBe(skinId);
  expect(container.querySelector(`[data-skin-art="${skinId}"]`)).not.toBeNull();
  RIG_SELECTORS.forEach((sel) => expect(container.querySelector(sel)).not.toBeNull());
}

test("skin: Mo is a base choice — he renders with no plan at all", () => {
  render({ skin: "mo" });
  expectRig("mo");
}, 15000);

test("skin: Empire plan unlocks Sterling", () => {
  render({ plan: "empire", skin: "sterling" });
  expectRig("sterling");
}, 15000);

test("skin: Empire plan unlocks Sloane", () => {
  render({ plan: "empire", skin: "sloane" });
  expectRig("sloane");
}, 15000);

test("skin: Empire plan unlocks The Foreman", () => {
  render({ plan: "empire", skin: "foreman" });
  expectRig("foreman");
}, 15000);

test("skin: a locked premium skin falls back to Mae on a base plan", () => {
  render({ plan: "pro", skin: "sterling" });
  expectRig("mae");
}, 15000);
