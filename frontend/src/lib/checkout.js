import { api } from "@/lib/api";

/** sessionStorage key for plan chosen on /pricing before auth completes */
export const PENDING_CHECKOUT_PLAN_KEY = "aio_pending_checkout_plan";

/** Paid plan keys resume Stripe Checkout; trial is free signup only. */
export function isPaidCheckoutPlan(planKey) {
  if (!planKey || typeof planKey !== "string") return false;
  const k = planKey.trim().toLowerCase();
  return k.length > 0 && k !== "trial" && k !== "enterprise";
}

export function setPendingCheckoutPlan(planKey) {
  if (!isPaidCheckoutPlan(planKey)) {
    try { sessionStorage.removeItem(PENDING_CHECKOUT_PLAN_KEY); } catch (_) { /* ignore */ }
    return;
  }
  try { sessionStorage.setItem(PENDING_CHECKOUT_PLAN_KEY, planKey.trim()); } catch (_) { /* ignore */ }
}

export function peekPendingCheckoutPlan() {
  try { return sessionStorage.getItem(PENDING_CHECKOUT_PLAN_KEY) || ""; } catch (_) { return ""; }
}

export function consumePendingCheckoutPlan() {
  const v = peekPendingCheckoutPlan();
  try { sessionStorage.removeItem(PENDING_CHECKOUT_PLAN_KEY); } catch (_) { /* ignore */ }
  return v;
}

/**
 * Call existing backend checkout (server resolves stripe_price_id).
 * Redirects the browser to Stripe Checkout URL on success.
 * @returns {Promise<{checkout_url: string, session_id: string}>}
 */
export async function startStripeCheckout(planId, { originUrl } = {}) {
  if (!isPaidCheckoutPlan(planId)) {
    throw new Error("Plan is not purchasable via Checkout");
  }
  const origin = originUrl || (typeof window !== "undefined" ? window.location.origin : "");
  const { data } = await api.post("/payments/checkout", {
    plan_id: planId,
    origin_url: origin,
  });
  if (!data?.checkout_url) {
    throw new Error("Checkout did not return a URL");
  }
  if (typeof window !== "undefined") {
    window.location.href = data.checkout_url;
  }
  return data;
}

/**
 * After login/signup: if a paid plan was pending, start Checkout.
 * @returns {Promise<boolean>} true if checkout redirect was started
 */
export async function resumePendingCheckoutIfAny() {
  const plan = consumePendingCheckoutPlan();
  if (!isPaidCheckoutPlan(plan)) return false;
  await startStripeCheckout(plan);
  return true;
}
