// Mascot skin system for the landing-page assistant (components/LeadAssistant.jsx).
//
// Every character shares ONE animation rig — the same pose groups (legs, arms,
// head) — so a "skin" is purely an artwork swap inside that rig and the walk /
// dance / come-alive motion is identical for every character. This module is the
// catalogue plus the tier gate: which subscription plans unlock which skins.
//
//   Base (every plan — the starting guy-or-gal choice):
//     mae      — Mae, the AI front-desk gal (headset bot).
//     mo       — Mo, her guy counterpart: same front desk, same palette family.
//   Empire-tier premium:
//     sterling — the little closer in a tailored suit, shades + gloves.
//     sloane   — the executive in a tailored blazer, shades + gloves.
//     foreman  — hard hat, hi-vis vest, clipboard under one arm.
//
// Where a visitor's plan isn't known (our public marketing pages), the host passes
// no plan: the base pair is available and the default skin (Mae) renders unless the
// host asks for Mo. Product surfaces that DO know the tenant's plan pass `plan`
// (and optionally a requested `skin`) to <LeadAssistant/>. Wiring a shop's own
// pages to their plan happens at deploy/integration time; the component-level gate
// proven here is what those surfaces will rely on.

export const MASCOT_PLANS = ["lite", "starter", "pro", "empire", "unlimited"];

const ALL_PLANS = ["lite", "starter", "pro", "empire", "unlimited"];

// Empire-tier premium skins; Unlimited sits above Empire and inherits them.
const EMPIRE_TIER = ["empire", "unlimited"];

export const MASCOT_SKINS = [
  { id: "mae", name: "Mae", unlockedBy: ALL_PLANS },
  { id: "mo", name: "Mo", unlockedBy: ALL_PLANS },
  { id: "sterling", name: "Sterling", unlockedBy: EMPIRE_TIER },
  { id: "sloane", name: "Sloane", unlockedBy: EMPIRE_TIER },
  { id: "foreman", name: "The Foreman", unlockedBy: EMPIRE_TIER },
];

export const DEFAULT_SKIN = "mae";

// Skin ids a plan may use. Unknown / missing plan → the base pair (base tier).
export function skinsForPlan(plan) {
  const raw = String(plan || "").toLowerCase();
  const p = MASCOT_PLANS.includes(raw) ? raw : "lite";
  return MASCOT_SKINS.filter((s) => s.unlockedBy.includes(p)).map((s) => s.id);
}

export function isSkinUnlocked(skinId, plan) {
  return skinsForPlan(plan).includes(skinId);
}

// The skin to actually render: the requested one when the plan unlocks it,
// otherwise the default. Never renders a locked skin.
export function resolveSkin(requested, plan) {
  if (requested && isSkinUnlocked(requested, plan)) return requested;
  return DEFAULT_SKIN;
}

export default MASCOT_SKINS;
