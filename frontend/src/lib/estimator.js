// Pure plan matching for the instant-quote estimator. No I/O; unit-tested in estimator.test.js.
import { ESTIMATOR_RULES } from "../config/estimatorRules";

const METRICS = ["calls", "sms", "ai_minutes", "locations", "users"];

function bandMax(bands, id) {
  const b = (bands || []).find((x) => x.id === id);
  if (!b) return undefined;
  return b.max === null ? Infinity : b.max;
}

const WHOLE_NUMBER = /^\d+$/;

/**
 * Validate the free-number answers. Zero, negative, blank, decimal or non-numeric values are
 * rejected with an error code instead of silently counting as "not answered" (which used to
 * fall through to Starter).
 * @returns {{locations?: "required"|"invalid"|"tooLarge", users?: "required"|"invalid"|"tooLarge"}}
 */
export function validateAnswers(answers = {}, rules = ESTIMATOR_RULES) {
  const errors = {};
  const caps = { locations: rules.maxLocationsInput, users: rules.maxUsersInput };
  for (const field of ["locations", "users"]) {
    const raw = answers[field];
    const str = raw === undefined || raw === null ? "" : String(raw).trim();
    if (str === "") errors[field] = "required";
    else if (!WHOLE_NUMBER.test(str) || Number(str) < 1) errors[field] = "invalid";
    else if (Number.isFinite(caps[field]) && Number(str) > caps[field]) errors[field] = "tooLarge";
  }
  return errors;
}

/** True when the AI-minutes need was estimated from calls because the answer was "Not sure". */
export function aiMinutesEstimated(answers = {}, rules = ESTIMATOR_RULES) {
  return answers.aiMinutesBand === rules.aiMinutesUnsureId && Number(rules.aiMinutesPerCallIfUnsure) > 0
    && bandMax(rules.callBands, answers.callsBand) !== undefined;
}

function aiMinutesNeed(answers, rules) {
  if (answers.aiMinutesBand !== rules.aiMinutesUnsureId) return bandMax(rules.aiMinutesBands, answers.aiMinutesBand);
  // "Not sure": conservative floor from the call band (placeholder ratio), never ignored.
  if (!aiMinutesEstimated(answers, rules)) return undefined;
  const calls = bandMax(rules.callBands, answers.callsBand);
  return calls === Infinity ? Infinity : Math.ceil(calls * Number(rules.aiMinutesPerCallIfUnsure));
}

/** Turn form answers into required capacity per metric (undefined = not asked/answered). */
export function requirementsFrom(answers = {}, rules = ESTIMATOR_RULES) {
  const toInt = (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : undefined;
  };
  return {
    calls: bandMax(rules.callBands, answers.callsBand),
    sms: bandMax(rules.smsBands, answers.smsBand),
    ai_minutes: aiMinutesNeed(answers, rules),
    locations: toInt(answers.locations),
    users: toInt(answers.users),
  };
}

/** Standard plans present in runtime data, in data order (sort_order, then price). */
export function standardPlans(plans, rules = ESTIMATOR_RULES) {
  if (!Array.isArray(plans)) return [];
  const wanted = new Set(rules.standardPlanKeys);
  return plans
    .filter((p) => p && wanted.has(p.key) && p.limits && typeof p.limits === "object" && Number(p.price_cents) > 0)
    .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.price_cents - b.price_cents);
}

/**
 * Match answers to a plan.
 * @returns {{status: "ok"|"custom"|"unavailable"|"invalid", plan: object|null, exceeded: string[],
 *   warnings: string[], errors?: object}}
 */
export function matchPlan(answers, plans, rules = ESTIMATOR_RULES) {
  const errors = validateAnswers(answers, rules);
  if (Object.keys(errors).length > 0) {
    return { status: "invalid", plan: null, exceeded: [], warnings: [], errors };
  }
  const warnings = [];
  const tiers = standardPlans(plans, rules);
  if (tiers.length === 0) {
    return { status: "unavailable", plan: null, exceeded: [], warnings: ["no_plan_data"] };
  }
  if (aiMinutesEstimated(answers, rules)) warnings.push("ai_minutes_estimated");
  for (const key of rules.standardPlanKeys) {
    if (!tiers.find((p) => p.key === key)) warnings.push(`missing_plan:${key}`);
  }
  const need = requirementsFrom(answers, rules);
  const headroom = Number(rules.headroom) > 0 ? Number(rules.headroom) : 1;

  const capMin = {};
  for (const c of rules.capabilities || []) capMin[c.id] = c.minPlan;
  let minIndex = 0;
  for (const cap of answers?.capabilities || []) {
    const minKey = capMin[cap];
    if (!minKey) continue;
    const idx = tiers.findIndex((p) => p.key === minKey);
    if (idx === -1) {
      warnings.push(`capability_plan_missing:${cap}`);
      continue;
    }
    minIndex = Math.max(minIndex, idx);
  }

  const fits = (plan) =>
    METRICS.every((m) => {
      if (need[m] === undefined) return true;
      const limit = plan.limits[m];
      if (typeof limit !== "number") return false; // unknown limit: can't confirm fit
      return need[m] * headroom <= limit;
    });

  for (let i = minIndex; i < tiers.length; i += 1) {
    if (fits(tiers[i])) return { status: "ok", plan: tiers[i], exceeded: [], warnings };
  }

  const top = tiers[tiers.length - 1];
  const exceeded = METRICS.filter((m) => {
    if (need[m] === undefined) return false;
    const limit = top.limits[m];
    return typeof limit !== "number" || need[m] * headroom > limit;
  });
  const custom = (Array.isArray(plans) ? plans : []).find((p) => p && p.key === rules.customPlanKey) || null;
  return { status: "custom", plan: custom, exceeded, warnings };
}

/** Exact price string from cents — never rounded to whole dollars. */
export function formatPrice(cents) {
  const n = Number(cents);
  if (!Number.isFinite(n)) return "";
  return `$${(n / 100).toFixed(2)}`;
}
