const { TextEncoder, TextDecoder } = require("util");
Object.assign(globalThis, { TextEncoder, TextDecoder });
const React = require("react");
const { act } = React;
const { createRoot } = require("react-dom/client");
const { computeRoi, parseInput, nicheConfig, BASE_ASSUMPTIONS, NICHES, COPY } = require("../../config/roi");
const RoiCalculator = require("./RoiCalculator").default;
const WorkflowDiagrams = require("./WorkflowDiagrams").default;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function mount(node) {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const root = createRoot(el);
  act(() => root.render(node));
  return { el, done: () => { act(() => root.unmount()); el.remove(); } };
}
function type(input, v) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  act(() => { setter.call(input, v); input.dispatchEvent(new Event("input", { bubbles: true })); });
}

describe("formula", () => {
  test("revenue and hours", () => {
    const r = computeRoi({ missedCalls: 20, jobValue: 300 }, { recoveryRate: 0.5, bookingRate: 0.25, minutesPerCall: 5 });
    expect(r.reached).toBe(10);
    expect(r.recoveredRevenue).toBe(750);
    expect(r.hoursSaved).toBeCloseTo(50 / 60);
  });
  test("zero inputs give zero", () => {
    expect(computeRoi({ missedCalls: 0, jobValue: 500 }, BASE_ASSUMPTIONS)).toEqual({ reached: 0, recoveredRevenue: 0, hoursSaved: 0 });
  });
  test("every niche has defaults and assumptions; unknown falls back to hvac", () => {
    for (const k of Object.keys(NICHES)) {
      expect(NICHES[k].defaults.missedCalls).toBeGreaterThanOrEqual(0);
      expect(NICHES[k].assumptions.recoveryRate).toBeLessThanOrEqual(1);
    }
    expect(nicheConfig("nope")).toBe(NICHES.hvac);
  });
});

describe("validation", () => {
  test.each([["", "errRequired"], ["   ", "errRequired"], ["abc", "errRequired"], ["-1", "errNegative"], ["2000000", "errTooLarge"]])("%p -> %s", (raw, err) => {
    expect(parseInput(raw).error).toBe(err);
  });
  test("whole numbers for calls", () => {
    expect(parseInput("2.5", { integer: true }).error).toBe("errWhole");
    expect(parseInput("2.5").value).toBe(2.5);
    expect(parseInput("0", { integer: true })).toEqual({ value: 0, error: null });
  });
});

describe("RoiCalculator", () => {
  test("renders defaults, estimates and assumptions", () => {
    const { el, done } = mount(<RoiCalculator niche="hvac" />);
    expect(el.querySelector('[data-testid="roi-revenue"]').textContent).toBe("≈ $750");
    expect(el.querySelector('[data-testid="roi-hours"]').textContent).toBe("≈ 0.8 hours");
    const as = el.querySelector('[data-testid="roi-assumptions"]').textContent;
    expect(as).toContain("50%"); expect(as).toContain("25%"); expect(as).toContain("5");
    expect(el.textContent).toContain("Estimated recovered revenue");
    expect(el.querySelector('[data-testid="roi-disclaimer"]').textContent).toBe(COPY.en.disclaimer);
    done();
  });
  test("updates live and rejects blank/negative input", () => {
    const { el, done } = mount(<RoiCalculator niche="hvac" />);
    const calls = el.querySelector('[data-testid="roi-missedCalls"]');
    const val = el.querySelector('[data-testid="roi-jobValue"]');
    type(calls, "40"); type(val, "500");
    expect(el.querySelector('[data-testid="roi-revenue"]').textContent).toBe("≈ $2,500");
    expect(el.querySelector('[data-testid="roi-hours"]').textContent).toBe("≈ 1.7 hours");
    type(calls, "");
    expect(el.querySelector('[data-testid="roi-missedCalls-error"]').textContent).toBe(COPY.en.errRequired);
    expect(calls.getAttribute("aria-invalid")).toBe("true");
    expect(el.querySelector('[data-testid="roi-revenue"]').textContent).toBe("—");
    type(calls, "10"); type(val, "-5");
    expect(el.querySelector('[data-testid="roi-jobValue-error"]').textContent).toBe(COPY.en.errNegative);
    expect(el.querySelector('[data-testid="roi-hours"]').textContent).toBe("—");
    done();
  });
  test("labels and uses the niche/override config; Spanish copy", () => {
    const { el, done } = mount(<RoiCalculator niche="roofing" lang="es" assumptions={{ bookingRate: 0.1 }} />);
    expect(el.querySelector("label").textContent).toBe(COPY.es.missedCalls);
    expect(el.querySelector('[data-testid="roi-missedCalls"]').value).toBe("10");
    // 10 × 0.5 × 0.1 × 1000 = 500
    expect(el.querySelector('[data-testid="roi-revenue"]').textContent.replace(/\s/g, " ")).toContain("500");
    done();
  });
  test("makes no network calls and stores nothing", () => {
    const fetchSpy = jest.fn(); globalThis.fetch = fetchSpy;
    localStorage.clear(); sessionStorage.clear();
    const { el, done } = mount(<RoiCalculator />);
    type(el.querySelector('[data-testid="roi-missedCalls"]'), "99");
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
    done();
  });
});

describe("WorkflowDiagrams", () => {
  test.each(["en", "es"])("three accessible SVGs (%s)", (lang) => {
    const { el, done } = mount(<WorkflowDiagrams lang={lang} />);
    const svgs = el.querySelectorAll("svg[role=img]");
    expect(svgs).toHaveLength(3);
    svgs.forEach((svg) => {
      const ids = svg.getAttribute("aria-labelledby").split(" ");
      expect(ids.every((id) => svg.querySelector(`[id="${id}"]`)?.textContent)).toBe(true);
      expect(svg.querySelector("title").textContent.length).toBeGreaterThan(0);
    });
    ["reception", "sales", "growth"].forEach((d) => expect(el.querySelector(`[data-testid="diagram-${d}"]`)).not.toBeNull());
    expect(el.querySelector("img")).toBeNull();
    done();
  });
});
