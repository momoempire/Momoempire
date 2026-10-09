# EMP-EXP-002 — ADHD children’s content subscription portal (one-page spec)

**Status:** Spec only. No product decisions locked. No signups, messages, or deploys.  
**Date:** 2026-10-08 (America/Chicago)  
**Related:** Clip Forge (teammate bot → short videos); Momoempire (AI Office Platform) for reusable platform pieces.

---

## Goal and audience

**Goal (hypothesis for Brann):** Parent-paid subscription of short, structure-friendly videos (routines, focus resets, transitions) from Clip Forge, watched in a low-distraction player.

| Who | Role |
| --- | --- |
| **Parents / caregivers** | Account holders, payers, consent-givers, controls |
| **Kids** | Viewers only (profiles under the parent—not independent legal accounts) |

Not a clinic, therapy product, or school LMS. Lifestyle/support content only (**not** diagnosis or treatment).

---

## Core features (options to confirm—not decided)

1. **Parent account + child profiles** — Adult login; named profiles (avatar/age band); parent picks who’s watching.
2. **Short-format library** — Themes: routines, focus/reset, transitions (length/series TBD).
3. **Parental controls** — PIN for settings; theme gates; daily time cap; optional bedtime window.
4. **Progress / streaks** — Light “done today” / streak; **no** ad/behavioral profiling.
5. **New-video drops** — Clip Forge ingest → human approval → publish → optional parent email.
6. **Offline / low-distraction player** — Minimal chrome, no autoplay spirals, optional offline download (TBD); honor reduced motion.

---

## Reuse Momoempire vs build new

| Reuse | New |
| --- | --- |
| Multi-tenant auth / JWT | Child profiles + parental PIN |
| Stripe plans + `stripe_price_id` Checkout (PR #1) | Kids library + low-distraction player |
| i18n EN/ES | Clip Forge ingest + **approval queue** |
| Resend email | COPPA consent UX; offline packs (if approved) |

Momoempire stays B2B AI office; this is a **separate product surface** that may share auth/billing/email/i18n—keep tenant/brand isolation.

---

## Clip Forge integration seam

1. **Ingest:** Clip Forge delivers MP4 (or agreed format) + metadata JSON: `title`, `theme`, `duration_sec`, `lang`, `suggested_age_band`, captions URI, optional warnings, `clip_forge_job_id`.
2. **Storage:** Object storage + DB row `status=pending_review`.
3. **Approval queue (required):** Human (Brann or delegate) approve/reject in admin **before** publish. No auto-publish.
4. **Publish / reject:** `published` → library + optional Resend “new drop”; reject returns notes to Clip Forge job id (format TBD).

---

## Compliance must-haves (non-negotiable framing)

- **COPPA:** Parent is sole account holder/purchaser. No child contact/location collection; no ads or behavioral tracking. Any child-linked data (even a profile name) needs **verifiable parental consent** + privacy notice—counsel before launch (this spec ≠ legal advice).
- **No medical claims.** Disclaimer direction: *not a substitute for professional care*; no diagnose/treat/cure copy.
- **Accessibility:** Captions, keyboard controls, contrast, reduced motion.
- **Ads:** None on the kid player; no cross-site tracking.

---

## Pricing hypotheses only (Brann decides)

Public comparables (USD; verify before launch)—**hypotheses only**, not decided prices:

| Hypothesis | Estimate | Cited comparable |
| --- | --- | --- |
| **H1 Monthly** | ~**$9.99–$13.99/mo** | Epic Family **$13.99/mo** ([Epic Help](https://support.getepic.com/hc/en-us/articles/204259899-How-much-does-Epic-Family-cost)) |
| **H2 Annual family** | ~**$79–$99/yr** | Epic Family **$84.99/yr** (same); Headspace Family **$99.99/yr** (≤6) ([Headspace Help](https://help.headspace.com/hc/en-us/articles/360010693034-What-does-the-Headspace-Family-plan-offer)) |
| **H3 Soft landing** | Free sample + paid unlock | Common kids-app pattern (e.g. limited free vs paid Family)—SKU is Brann’s call |

Wire chosen prices via existing Stripe `stripe_price_id` plans.

---

## Build steps (effort = rough estimates)

Basis: one full-stack eng familiar with Momoempire; no new design system; excludes counsel and Clip Forge model work.

| Step | Scope | Effort (estimate) |
| --- | --- | --- |
| 1 | Parent auth, Stripe plan(s), EN/ES | **3–5 days** |
| 2 | Profiles, parental controls, consent/disclaimer | **3–5 days** |
| 3 | Library, player, streaks | **5–8 days** |
| 4 | Ingest API, approval queue, drop email | **4–6 days** |
| 5 | Privacy/a11y sweep + thin pilot | **3–5 days** (+ legal) |

**Total:** ~**4–6 engineer-weeks** to a thin pilot (estimate).

---

## Risks

COPPA missteps; medical-claim creep; auto-publish; thin library vs churn; chargebacks; B2B brand collision; offline/DRM scope creep.

---

## Decisions only Brann can make

1. Greenlight vs Clip Forge → social-only?  
2. Brand/domain; shared Momoempire infra vs separate app?  
3. Pricing hypothesis (H1/H2/H3) and profile count?  
4. v1 themes / age bands?  
5. Who owns the approval queue?  
6. Engage COPPA/privacy counsel before child-profile data?  
7. Offline downloads in v1 or later?

---

*Expansion doc only—does not authorize spend, signup, or messaging.*
