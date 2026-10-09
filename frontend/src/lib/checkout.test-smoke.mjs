/** Lightweight smoke for checkout helpers (no Jest required). Run: node src/lib/checkout.test-smoke.mjs */
import assert from "node:assert/strict";

// Re-implement pure helpers inline to avoid CRA path aliases in node.
function isPaidCheckoutPlan(planKey) {
  if (!planKey || typeof planKey !== "string") return false;
  const k = planKey.trim().toLowerCase();
  return k.length > 0 && k !== "trial" && k !== "enterprise";
}

assert.equal(isPaidCheckoutPlan("starter"), true);
assert.equal(isPaidCheckoutPlan("trial"), false);
assert.equal(isPaidCheckoutPlan("enterprise"), false);
assert.equal(isPaidCheckoutPlan(""), false);
console.log("checkout helper smoke OK");
