// =====================================================================================
// PLACEHOLDER — Brann to confirm.
// Instant-quote estimator (lead qualifier) mapping rules. Every threshold and mapping in this
// file was chosen by Momoempire Builder as a starting point and is NOT a product decision.
// Plan names, prices and limits are NOT here: they come from GET /api/plans at runtime.
// =====================================================================================

export const ESTIMATOR_RULES_STATUS = "PLACEHOLDER — Brann to confirm";

export const ESTIMATOR_RULES = {
  // Paid plans considered, by key (must exist in GET /api/plans). Order comes from the data.
  standardPlanKeys: ["starter", "growth", "ai_office", "high_volume"],
  // Shown only when answers exceed every standard plan (name taken from plan data if present).
  customPlanKey: "enterprise",

  // PLACEHOLDER: required capacity = band max × headroom must be ≤ the plan's limit.
  headroom: 1.0,

  // PLACEHOLDER: monthly volume bands. `max` = upper bound used for matching;
  // `max: null` = open-ended (always beyond standard plans → Custom).
  callBands: [
    { id: "calls_1", max: 150 },
    { id: "calls_2", max: 500 },
    { id: "calls_3", max: 1500 },
    { id: "calls_4", max: 4000 },
    { id: "calls_5", max: null },
  ],
  smsBands: [
    { id: "sms_0", max: 0 },
    { id: "sms_1", max: 300 },
    { id: "sms_2", max: 1000 },
    { id: "sms_3", max: 3000 },
    { id: "sms_4", max: 8000 },
    { id: "sms_5", max: null },
  ],

  // PLACEHOLDER: AI-answered minutes per month, matched against plan.limits.ai_minutes.
  // Upper bounds mirror today's seeded ai_minutes limits (backend/routers/plans.py) so each band
  // lands on one plan; Brann to confirm. "ai_unsure" has no max: not counted in matching.
  aiMinutesBands: [
    { id: "ai_1", max: 100 },
    { id: "ai_2", max: 300 },
    { id: "ai_3", max: 650 },
    { id: "ai_4", max: 1400 },
    { id: "ai_5", max: null },
  ],
  aiMinutesUnsureId: "ai_unsure",

  // Input caps for the number fields (not tier thresholds).
  maxLocationsInput: 999,
  maxUsersInput: 9999,

  // Capabilities the product has today (each has a working dashboard page + API).
  // PLACEHOLDER minPlan: the code does NOT gate these by plan; mapping follows the plan
  // `features` text in backend/routers/plans.py where it mentions them, otherwise null
  // (= any paid plan) until Brann decides.
  capabilities: [
    { id: "ai_receptionist", minPlan: "starter" },   // "Basic AI receptionist" (Starter)
    { id: "booking", minPlan: "growth" },            // "Appointment booking" (Growth)
    { id: "reviews", minPlan: "ai_office" },         // "Reviews" (AI Office)
    { id: "customer_portal", minPlan: "ai_office" }, // "Customer portal" (AI Office)
    { id: "business_advisor", minPlan: "ai_office" },// "AI business advisor" (AI Office)
    { id: "sales_intel", minPlan: null },            // not in any plan's features list — TODO Brann
    { id: "growth_autopilot", minPlan: null },       // not in any plan's features list — TODO Brann
    { id: "payments", minPlan: null },               // not in any plan's features list — TODO Brann
  ],
};
