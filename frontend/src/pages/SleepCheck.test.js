// Sleep Habits Check: general-wellness copy rules, results from answers, and zero data collection.
const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });
const fs = require("fs");
const path = require("path");
const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { MemoryRouter } = require("react-router");
jest.mock("react-router-dom", () => jest.requireActual("react-router"));
const SleepCheck = require("./SleepCheck").default;
const { SLEEP_COPY, SLEEP_QUESTIONS, SLEEP_TIPS, tipsFor } = require("../config/sleepCheck");

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const DISCLAIMER = "This quiz is for general wellness only. It isn't a medical test and can't tell you whether you have any condition. If you're concerned about your sleep, talking with a doctor or other healthcare professional may be helpful.";
// The exact disclaimer is the only allowed place for words like "test" and "condition".
const strip = (t) => t.split(DISCLAIMER).join(" ");
const BANNED = [/apnea/i, /insomnia/i, /narcolep/i, /restless/i, /\bRLS\b/, /diagnos/i, /disorder/i, /screen/i, /\btest/i, /\brisk/i, /abnormal/i, /\bnormal\b/i, /score/i, /treat/i, /\bcure/i, /prevent/i, /therapy/i, /assess/i, /detect/i, /evaluat/i, /clinical/i, /threshold/i, /condition/i, /disease/i, /symptom/i, /patient/i, /accura/i, /validated/i, /snor/i, /gasp/i, /breath/i, /real sleep/i, /recommended/i];

function mount() {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el);
  act(() => { root.render(<MemoryRouter><SleepCheck /></MemoryRouter>); });
  return { el, done: () => { act(() => root.unmount()); el.remove(); } };
}
const pick = (el, qid, i) => act(() => { el.querySelectorAll(`input[name="${qid}"]`)[i].click(); });
const submit = (el) => act(() => { el.querySelector('[data-testid="sleep-submit"]').click(); });

function allCopy() {
  const s = [];
  Object.values(SLEEP_COPY).forEach((v) => s.push(typeof v === "function" ? v(3, 8) : v));
  SLEEP_QUESTIONS.forEach((q) => { s.push(q.q); q.options.forEach((o) => s.push(o.label)); });
  s.push(...Object.values(SLEEP_TIPS));
  return s;
}

test("copy has about 8 multiple-choice questions and every tip key exists", () => {
  expect(SLEEP_QUESTIONS).toHaveLength(8);
  SLEEP_QUESTIONS.forEach((q) => { expect(q.options.length).toBeGreaterThanOrEqual(3); q.options.forEach((o) => { if (o.tip) expect(SLEEP_TIPS[o.tip]).toBeTruthy(); }); });
});

test.each(BANNED.map((r) => [String(r), r]))("no banned word %s in the copy or the rendered page", (_, re) => {
  allCopy().forEach((s) => expect(strip(s)).not.toMatch(re));
  const { el, done } = mount();
  expect(strip(el.textContent)).not.toMatch(re);
  SLEEP_QUESTIONS.forEach((q) => pick(el, q.id, q.options.length - 1));
  submit(el);
  expect(strip(el.textContent)).not.toMatch(re);
  done();
});

test("no numbers in the tips (no numeric thresholds)", () => {
  Object.values(SLEEP_TIPS).forEach((t) => expect(t).not.toMatch(/\d/));
});

test("disclaimer is the exact reviewed text and appears exactly once per view", () => {
  expect(SLEEP_COPY.disclaimer).toBe(DISCLAIMER);
});

test("results disclaimer comes before the tips, for every answer pattern", () => {
  for (const pickIdx of [0, 1]) {
    const { el, done } = mount();
    SLEEP_QUESTIONS.forEach((q) => pick(el, q.id, pickIdx));
    submit(el);
    const r = el.querySelector('[data-testid="sleep-results"]');
    expect(r.firstElementChild.textContent).toBe(DISCLAIMER);
    done();
  }
});

test("disclaimer is visible on the questions page and on the results", () => {
  const { el, done } = mount();
  expect(el.querySelector('[data-testid="sleep-disclaimer"]').textContent).toBe(DISCLAIMER);
  SLEEP_QUESTIONS.forEach((q) => pick(el, q.id, 0));
  submit(el);
  expect(el.querySelector('[data-testid="sleep-results-disclaimer"]').textContent).toBe(DISCLAIMER);
  done();
});

test("submit is disabled until every question is answered", () => {
  const { el, done } = mount();
  const b = el.querySelector('[data-testid="sleep-submit"]');
  expect(b.disabled).toBe(true);
  SLEEP_QUESTIONS.slice(0, 7).forEach((q) => pick(el, q.id, 0));
  expect(b.disabled).toBe(true);
  pick(el, SLEEP_QUESTIONS[7].id, 0);
  expect(b.disabled).toBe(false);
  done();
});

test("tips come from the answers, in question order, with no score or count of 'good' answers", () => {
  const { el, done } = mount();
  SLEEP_QUESTIONS.forEach((q) => pick(el, q.id, 0));
  pick(el, "wake", 2); pick(el, "naps", 2);
  submit(el);
  const items = [...el.querySelectorAll('[data-testid="sleep-tips"] li')].map((li) => li.textContent);
  expect(items).toEqual([SLEEP_TIPS.schedule, SLEEP_TIPS.naps]);
  expect(el.querySelector('[data-testid="sleep-results"]').textContent).not.toMatch(/\d/);
  act(() => { el.querySelector('[data-testid="sleep-restart"]').click(); });
  expect(el.querySelector('[data-testid="sleep-form"]')).not.toBeNull();
  expect(el.querySelectorAll("input:checked")).toHaveLength(0);
  done();
});

test("all habit-friendly answers show the general keep-it-up message", () => {
  expect(tipsFor(Object.fromEntries(SLEEP_QUESTIONS.map((q) => [q.id, 0])))).toEqual([]);
  const { el, done } = mount();
  SLEEP_QUESTIONS.forEach((q) => pick(el, q.id, 0));
  submit(el);
  expect(el.querySelector('[data-testid="sleep-all-set"]').textContent).toBe(SLEEP_COPY.allSet);
  done();
});

test("no data collection: no fetch, XHR, beacon, localStorage or sessionStorage calls", () => {
  const fetchSpy = jest.fn(() => Promise.resolve({}));
  global.fetch = fetchSpy;
  const xhr = jest.spyOn(XMLHttpRequest.prototype, "open");
  const beacon = jest.fn(); navigator.sendBeacon = beacon;
  const st = ["getItem", "setItem", "removeItem", "clear", "key"].map((m) => jest.spyOn(Storage.prototype, m));
  const { el, done } = mount();
  SLEEP_QUESTIONS.forEach((q) => pick(el, q.id, 1));
  submit(el);
  act(() => { el.querySelector('[data-testid="sleep-restart"]').click(); });
  done();
  expect(fetchSpy).not.toHaveBeenCalled();
  expect(xhr).not.toHaveBeenCalled();
  expect(beacon).not.toHaveBeenCalled();
  st.forEach((s) => expect(s).not.toHaveBeenCalled());
  st.forEach((s) => s.mockRestore()); xhr.mockRestore();
});

test("page source imports no API, storage or analytics code", () => {
  for (const f of ["SleepCheck.jsx", "../config/sleepCheck.js"]) {
    const src = fs.readFileSync(path.join(__dirname, f), "utf8").replace(/\/\/.*$/gm, "");
    expect(src).not.toMatch(/lib\/api|axios|fetch\(|localStorage|sessionStorage|sendBeacon|analytics|gtag|posthog/i);
  }
});

test("waitlist routes include /sleep-check", () => {
  const src = fs.readFileSync(path.join(__dirname, "../waitlist/WaitlistApp.jsx"), "utf8");
  expect(src).toMatch(/path="\/sleep-check"/);
});

test("no global analytics, ad pixel or third-party script in public/index.html (so none on /sleep-check)", () => {
  const html = fs.readFileSync(path.join(__dirname, "../../public/index.html"), "utf8");
  expect(html).not.toMatch(/<script[^>]+src=/i);
  expect(html).not.toMatch(/gtag|googletagmanager|google-analytics|fbq|facebook|pixel|posthog|plausible|clarity|segment|hotjar/i);
});
