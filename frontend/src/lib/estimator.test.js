import { matchPlan, requirementsFrom, standardPlans, formatPrice, validateAnswers } from "./estimator";
import { ESTIMATOR_RULES, ESTIMATOR_RULES_STATUS } from "../config/estimatorRules";

// Mirrors DEFAULT_PLANS in backend/routers/plans.py (shape of GET /api/plans).
const PLANS = [
  { key: "trial", name: "Free Trial / Sandbox", price_cents: 0, sort_order: 1,
    limits: { ai_minutes: 30, calls: 50, sms: 100, locations: 1, users: 1 } },
  { key: "starter", name: "Starter", price_cents: 1999, sort_order: 10,
    limits: { ai_minutes: 100, calls: 150, sms: 300, locations: 1, users: 2 } },
  { key: "growth", name: "Growth", price_cents: 4999, sort_order: 20,
    limits: { ai_minutes: 300, calls: 500, sms: 1000, locations: 1, users: 5 } },
  { key: "ai_office", name: "AI Office", price_cents: 9999, sort_order: 30,
    limits: { ai_minutes: 650, calls: 1500, sms: 3000, locations: 2, users: 10 } },
  { key: "high_volume", name: "High Volume", price_cents: 19999, sort_order: 40,
    limits: { ai_minutes: 1400, calls: 4000, sms: 8000, locations: 5, users: 25 } },
  { key: "enterprise", name: "Enterprise", price_cents: 0, interval: "custom", sort_order: 50, limits: {} },
];

const base = { callsBand: "calls_1", smsBand: "sms_1", aiMinutesBand: "ai_1", locations: 1, users: 1, capabilities: [] };
const keyOf = (a, plans = PLANS) => {
  const r = matchPlan({ ...base, ...a }, plans);
  return r.status === "ok" ? r.plan.key : r.status;
};

test("rules file is marked placeholder", () => {
  expect(ESTIMATOR_RULES_STATUS).toMatch(/PLACEHOLDER — Brann to confirm/);
});

test("smallest answers -> Starter (trial and enterprise never matched as standard)", () => {
  expect(keyOf({})).toBe("starter");
  expect(standardPlans(PLANS).map((p) => p.key)).toEqual(["starter", "growth", "ai_office", "high_volume"]);
});

test.each([
  ["calls_1", "starter"], ["calls_2", "growth"], ["calls_3", "ai_office"], ["calls_4", "high_volume"], ["calls_5", "custom"],
])("call band %s -> %s", (band, expected) => {
  expect(keyOf({ callsBand: band })).toBe(expected);
});

test.each([
  ["sms_0", "starter"], ["sms_1", "starter"], ["sms_2", "growth"], ["sms_3", "ai_office"], ["sms_4", "high_volume"], ["sms_5", "custom"],
])("sms band %s -> %s", (band, expected) => {
  expect(keyOf({ smsBand: band })).toBe(expected);
});

test.each([
  [{ users: 2 }, "starter"], [{ users: 3 }, "growth"], [{ users: 5 }, "growth"], [{ users: 6 }, "ai_office"],
  [{ users: 10 }, "ai_office"], [{ users: 11 }, "high_volume"], [{ users: 25 }, "high_volume"], [{ users: 26 }, "custom"],
  [{ locations: 1 }, "starter"], [{ locations: 2 }, "ai_office"], [{ locations: 5 }, "high_volume"], [{ locations: 6 }, "custom"],
])("boundary %o -> %s", (a, expected) => {
  expect(keyOf(a)).toBe(expected);
});

test("Custom only when beyond High Volume on some metric; reports exceeded metrics", () => {
  const atTop = matchPlan({ ...base, callsBand: "calls_4", smsBand: "sms_4", locations: 5, users: 25 }, PLANS);
  expect(atTop.status).toBe("ok");
  expect(atTop.plan.key).toBe("high_volume");
  const beyond = matchPlan({ ...base, users: 26, locations: 6 }, PLANS);
  expect(beyond.status).toBe("custom");
  expect(beyond.plan.key).toBe("enterprise");
  expect(beyond.exceeded.sort()).toEqual(["locations", "users"]);
});

test("capabilities raise the minimum tier (placeholder mapping)", () => {
  expect(keyOf({ capabilities: ["ai_receptionist"] })).toBe("starter");
  expect(keyOf({ capabilities: ["booking"] })).toBe("growth");
  expect(keyOf({ capabilities: ["reviews"] })).toBe("ai_office");
  expect(keyOf({ capabilities: ["sales_intel", "growth_autopilot", "payments"] })).toBe("starter"); // null = any paid plan
  expect(keyOf({ capabilities: ["booking"], users: 11 })).toBe("high_volume");
  expect(keyOf({ capabilities: ["reviews"], users: 26 })).toBe("custom");
});

test("uses runtime limits, not hardcoded numbers", () => {
  const bigger = PLANS.map((p) => (p.key === "starter" ? { ...p, limits: { ...p.limits, users: 50 } } : p));
  expect(keyOf({ users: 40 }, bigger)).toBe("starter");
});

test("missing plan data -> unavailable (no prices, no guess)", () => {
  for (const plans of [undefined, null, [], "oops", [{ key: "trial", price_cents: 0, limits: {} }]]) {
    const r = matchPlan(base, plans);
    expect(r.status).toBe("unavailable");
    expect(r.plan).toBeNull();
  }
});

test("partial plan data: missing tiers skipped with warnings; unknown limit never matched", () => {
  const partial = PLANS.filter((p) => p.key !== "growth");
  const r = matchPlan({ ...base, users: 3 }, partial);
  expect(r.plan.key).toBe("ai_office");
  expect(r.warnings).toContain("missing_plan:growth");
  const noUsersLimit = PLANS.map((p) => (p.key === "starter" ? { ...p, limits: { ai_minutes: 100, calls: 150, sms: 300, locations: 1 } } : p));
  expect(keyOf({ users: 1 }, noUsersLimit)).toBe("growth");
  const noEnterprise = PLANS.filter((p) => p.key !== "enterprise");
  const c = matchPlan({ ...base, callsBand: "calls_5" }, noEnterprise);
  expect(c.status).toBe("custom");
  expect(c.plan).toBeNull();
});

test("capability whose min plan is missing from data is ignored with a warning", () => {
  const noAiOffice = PLANS.filter((p) => p.key !== "ai_office");
  const r = matchPlan({ ...base, capabilities: ["reviews"] }, noAiOffice);
  expect(r.plan.key).toBe("starter");
  expect(r.warnings).toContain("capability_plan_missing:reviews");
});

test("requirements parsing ignores junk", () => {
  expect(requirementsFrom({ callsBand: "nope", users: "abc", locations: -2 })).toEqual({
    calls: undefined, sms: undefined, ai_minutes: undefined, locations: undefined, users: undefined,
  });
});

test("prices are exact, never rounded", () => {
  expect(formatPrice(1999)).toBe("$19.99");
  expect(formatPrice(4999)).toBe("$49.99");
  expect(formatPrice(9999)).toBe("$99.99");
  expect(formatPrice(19999)).toBe("$199.99");
});

// ---------- AI minutes (Watcher: calls fit but minutes could overrun) ----------
test.each([
  ["ai_1", "starter"], ["ai_2", "growth"], ["ai_3", "ai_office"], ["ai_4", "high_volume"], ["ai_5", "custom"],
])("AI minutes band %s -> %s", (band, expected) => {
  expect(keyOf({ aiMinutesBand: band })).toBe(expected);
});

test("AI minutes bands mirror the seeded ai_minutes limits", () => {
  const maxes = ESTIMATOR_RULES.aiMinutesBands.map((b) => b.max);
  expect(maxes).toEqual([100, 300, 650, 1400, null]);
});

test("calls fit AI Office but minutes do not -> next plan up", () => {
  const r = matchPlan({ ...base, callsBand: "calls_3", aiMinutesBand: "ai_4" }, PLANS);
  expect(r.plan.key).toBe("high_volume");
  const over = matchPlan({ ...base, callsBand: "calls_3", aiMinutesBand: "ai_5" }, PLANS);
  expect(over.status).toBe("custom");
  expect(over.exceeded).toEqual(["ai_minutes"]);
});

// EMP-W-CF-032: "Not sure" must not under-suggest: AI minutes floor = call band max x placeholder ratio.
const UNSURE = ESTIMATOR_RULES.aiMinutesUnsureId;
test.each([
  ["calls_1", "starter"],       // 150 x 0.5 = 75 <= 100
  ["calls_2", "growth"],        // 500 x 0.5 = 250 <= 300
  ["calls_3", "high_volume"],   // 1,500 x 0.5 = 750 > 650 (AI Office) -> High Volume
  ["calls_4", "custom"],        // 4,000 x 0.5 = 2,000 > 1,400
  ["calls_5", "custom"],
])("'Not sure' AI minutes with %s -> %s (conservative floor)", (callsBand, expected) => {
  expect(keyOf({ aiMinutesBand: UNSURE, callsBand })).toBe(expected);
});

test("'Not sure' never suggests a smaller plan than the explicit band would at that ratio", () => {
  const r = matchPlan({ ...base, aiMinutesBand: UNSURE, callsBand: "calls_4" }, PLANS);
  expect(r.exceeded).toEqual(["ai_minutes"]);
  expect(r.warnings).toContain("ai_minutes_estimated");
  expect(requirementsFrom({ aiMinutesBand: UNSURE, callsBand: "calls_3" }).ai_minutes).toBe(750);
  expect(matchPlan({ ...base, aiMinutesBand: "ai_1" }, PLANS).warnings).not.toContain("ai_minutes_estimated");
});

test("'Not sure' floor is a placeholder: ratio 0 ignores it again", () => {
  const rules = { ...ESTIMATOR_RULES, aiMinutesPerCallIfUnsure: 0 };
  expect(matchPlan({ ...base, aiMinutesBand: UNSURE, callsBand: "calls_3" }, PLANS, rules).plan.key).toBe("ai_office");
  expect(ESTIMATOR_RULES.aiMinutesPerCallIfUnsure).toBe(0.5);
});

test("plan without an ai_minutes limit is not matched when minutes were answered", () => {
  const noMinutes = PLANS.map((p) => (p.key === "starter" ? { ...p, limits: { calls: 150, sms: 300, locations: 1, users: 2 } } : p));
  expect(keyOf({}, noMinutes)).toBe("growth");
});

// ---------- input validation (no silent Starter) ----------
test.each([
  [0, "invalid"], [-1, "invalid"], ["-3", "invalid"], ["", "required"], [null, "required"], [undefined, "required"],
  ["abc", "invalid"], [NaN, "invalid"], ["2.9", "invalid"], [2.5, "invalid"], ["1e3", "invalid"], [" ", "required"],
])("users=%p -> %s (not Starter)", (users, code) => {
  const r = matchPlan({ ...base, users }, PLANS);
  expect(r.status).toBe("invalid");
  expect(r.plan).toBeNull();
  expect(r.errors.users).toBe(code);
});

test("locations validated too; too large is flagged", () => {
  expect(matchPlan({ ...base, locations: 0 }, PLANS).errors).toEqual({ locations: "invalid" });
  expect(validateAnswers({ ...base, locations: 1000 })).toEqual({ locations: "tooLarge" });
  expect(validateAnswers({ ...base, users: "10000" })).toEqual({ users: "tooLarge" });
});

test("valid whole numbers (including numeric strings from inputs) pass", () => {
  expect(validateAnswers({ locations: "2", users: " 3 " })).toEqual({});
  expect(keyOf({ users: "3" })).toBe("growth");
});
