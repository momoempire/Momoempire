# AI Office Platform — Roadmap

> Target: surpass ServiceTitan ($245–500/tech/mo, 6–12 mo implementations) with an
> AI-native platform, flat pricing, and deploy-in-days onboarding.
> Wedge: the small shops ServiceTitan prices out.

## Shipped

- **Rounds 1 & 2** (Cloudflare-2 branch): Cloudflare Pages prep, portable backend/Docker,
  quotes → jobs → invoices → payments via Stripe Connect, AI receptionist, AI lead
  scoring, SMS conversations, automations, reviews/testimonials, customer portal,
  recurring scheduling, usage metering + overage billing.
- **Cleanup pass** (`cleanup/cloudflare-2-polish`): README rewrite, reproducible builds
  (frozen lockfile, Node 20 pin), HSTS header, HANDOFF refresh, models consolidated
  (`models_phase2/3/4` → domain-organized `models.py`). Router audit: 0 duplicates.
- **Dispatch board** (`feature/dispatch-board`): per-tech day board, drag-and-drop
  assign/move, status flow (scheduled → en_route → on_site → completed), 12/12 tests.
- **GPS tracking** (`feature/gps-tracking`): live tech locations (tenant-scoped
  `tech_locations` collection, current + capped 50-point history), customer ETA
  page at `/track/:token` (public, token-scoped, first-name-only, zero customer
  PII), "tech's on the way" ETA SMS on en_route (v1: haversine at ~30mph;
  degrades to "On the way" without coords), dispatch-board live badges. 22/22 tests.
- **Equipment history** (`feature/equipment-history`): per-customer-location equipment
  records (make/model/serial, install date, full service address) + service
  timeline with job linkage; adds the missing service-address field to the data
  model (optional `service_address` on Customer; equipment always carries one).
  13/13 tests.

## Phase A — ServiceTitan parity gaps (build in order, one per checkpoint)

1. **Pricebook** — flat-rate pricebook: services, parts, labor matrices; versioned;
   feeds quotes/invoices/dispatch with consistent pricing.
2. **Equipment history** ✅ done (`feature/equipment-history`) — per-customer-location
   equipment records (make/model/serial, install date, service history);
   adds the missing service-address field to the data model (appointments/
   customers have none today — customers now accept an optional
   `service_address`, equipment records always carry the full address).
3. **GPS tech tracking** — "tech's on the way" live map + ETA texts for customers. **Done** (see Shipped).
4. **Financing at checkout** ✅ done (`feature/financing`) — provider-abstracted
   customer financing embedded in quotes/invoices: `FinancingProvider` ABC with
   `DemoFinancingProvider` (deterministic amortization math, clearly labeled
   demo) and a `WisetackProvider` skeleton with `TODO(LIVE)` markers (no live
   calls). Monthly-payment offers (12/24/36/48/60-mo, incl. 0% APR promo line)
   on the public quote/invoice pages via token-authenticated endpoints; per-
   tenant settings (enable toggle, provider select) in Settings → Financing,
   writes owner/admin-only. Shops will connect their OWN Wisetack merchant
   accounts later (Stripe-Connect model) — no platform keys needed.
5. **Bookkeeping link** — sync customers, invoices, payments with our OWN
   bookkeeping app (the QuickBooks competitor), not QuickBooks. Provider-abstracted
   adapter; per-tenant encrypted API token; auto-sync on invoice-paid. (Replaces
   the earlier QuickBooks-sync plan per user directive 2026-10-15.)
6. **AI photo estimates** ✅ done (`feature/ai-photo-estimates`) — customer sends a
   photo, AI generates a draft estimate (leans on the pricebook from #1).
   `VisionProvider` ABC + deterministic `MockVisionProvider`; live Gemini
   (`GEMINI_API_KEY`, free tier) and OpenAI-compatible providers are skeletons
   with TODO(LIVE) markers and a mock fallback so the flow always works in
   demo. `/tenants/photo-estimates`: upload (image-MIME + 10MB validation) →
   analyze (Jaccard description-match against active pricebook rows, confidence
   surfaced, unmatched → generic lines) → approve (human edit step) → flows
   into the existing `_create_estimate_doc` quote-draft pipeline, nothing
   duplicated. Part B: `llm_portable.py` now supports `GEMINI_API_KEY`
   (google-genai==2.26.0); resolution EMERGENT → OPENAI → GEMINI → raise,
   completing the "Claude/Gemini later" TODO for Gemini.
   Frontend: `/app/photo-estimates` page (upload → review suggestions w/
   confidence + pricebook matches → edit/approve → create quote draft), EN+ES.

**Phase A is COMPLETE** — all 6 ServiceTitan parity gaps shipped (pricebook,
equipment history, GPS tracking, financing, bookkeeping link, AI photo
estimates). Next: Phase B (AI dispatcher, AI onboarding, predictive revenue
engine, outbound voice AI, per-job profitability, price intelligence).

## Phase B — Beyond parity (the distance-makers)

7. **AI dispatcher** — the board assigns itself: skill + location + traffic +
   profitability optimization, human approves. (Builds on dispatch board.)
8. **AI onboarding** — shop uploads old invoices/customer list; AI builds their
   pricebook, customers, and service history in an afternoon. Kills ServiceTitan's
   implementation-moat advantage and becomes the #1 sales pitch. Includes
   **Twilio subaccount auto-provisioning**: each new business automatically gets
   its own Twilio subaccount + dedicated number(s) at signup (platform holds one
   parent account); existing numbers portable in. Required for the 270-business
   plan — one shared number cannot serve many businesses (branding, 10DLC
   compliance, throughput, reputation isolation).
9. **Predictive revenue engine** — equipment age + season + history → auto-generated
   replacement/upsell quotes with financing pre-attached, one tap to send.
10. **Outbound voice AI** — missed-call callbacks in 60s, overdue-invoice calls,
    reschedule nudges, seasonal tune-up campaigns. A sales team that never sleeps.
11. **Real-time per-job profitability** — techs see live job margin in the driveway;
    owner dashboards move from backward-looking to live.
12. **Price intelligence network** — anonymized cross-shop pricing benchmarks
    ("shops like yours in Dallas charge $X"). Uncopyable data moat at scale.

## Phase C — Exploratory (high upside, validate before building)

13. **AI call coaching for CSRs** — score every inbound call; flag missed bookings
    ("you lost 14 bookings this week — nobody offered same-day").
14. **Warranty & callback tracking** — auto-escalate repeat visits for the same unit
    ("3rd visit in 60 days"); plugs a silent profit leak.
15. **Supplier-fed pricebook updates** — distributor cost feeds auto-update flat-rate
    prices when parts costs change. (Builds on pricebook.)
16. **AI triage self-booking** — website widget: customer describes the problem in
    plain words, AI triages urgency and offers real slots. Converts better than forms.
17. **Subcontractor management** — dispatch, pay, and 1099 subs through the platform.
18. **Trade expansion packs** — the niche-pack strategy from the bookkeeping app,
    and it fits this platform even harder. Each pack is a one-click vertical setup:
    pre-built pricebook (trade-specific services + parts), equipment types for
    equipment history, appointment types + durations, AI receptionist scripts and
    trade-specific triage knowledge, automation templates (seasonal reminders,
    maintenance plans), and review-request templates.
    Unified niche list — 33 packs (bookkeeping's 20 + 13 additions):
    1. HVAC, 2. plumbing, 3. electrical, 4. roofing, 5. appliance repair,
    6. garage door, 7. pest control, 8. landscaping, 9. pool service, 10. painting,
    11. handyman, 12. solar, 13. cleaning, 14. tree service, 15. fencing & concrete,
    16. restoration, 17. auto repair, 18. barbershop, 19. real estate,
    20. freelancer, 21. locksmith, 22. septic & well,
    23. kitchen & bath remodeling, 24. windows & doors, 25. flooring,
    26. foundation repair, 27. whole-home generators, 28. smart home & security,
    29. junk removal, 30. decks & outdoor living, 31. irrigation,
    32. siding & gutters, 33. home inspection.
    Launch order: HVAC first (Mind at Ease HVAC is the reference brand), then
    plumbing, electrical, roofing — then the rest in list order.

## Phase D — Shine-brighter differentiators (post-launch)

19. **Homeowner home-hub app** — consumer-facing: systems, warranties, service
    history, maintenance reminders, one-tap booking. Makes us a consumer brand,
    not just shop software. Biggest swing.
20. **Video estimates** — customer records a walkthrough video; AI extracts full
    scope into a draft estimate. Builds on AI photo estimates.
21. **Parts ordering from the job** — tech diagnoses, taps once, distributor order
    placed. Kills supply-house runs.
22. **AI collections with empathy** — persistent but polite AI follow-up on overdue
    invoices (text → voice → escalate). No burned relationships.
23. **Maintenance agreement autopilot** — auto-renewals, auto-scheduled tune-ups,
    churn prediction before members cancel. Recurring-revenue machine.
24. **Tech gamification** — leaderboards on close rate, ticket size, callbacks.
    Retention + performance.

## Phase E — Endgame: the autonomous shop (A++)

The shop that runs itself. Zero human effort; humans handle exceptions only.

25. **Zero-touch job lifecycle** — quote → AI follow-up → approval → auto-dispatch →
    routed → completed → invoiced → paid → review requested. Builds on: dispatch,
    AI dispatcher, outbound voice, AI collections.
26. **Dynamic pricing engine** — AI adjusts flat-rate prices for demand, season,
    tech availability (emergency premiums, shoulder-season fill discounts).
    Builds on: pricebook.
27. **Self-healing operations** — auto-retry failed webhooks/payments, auto-resend
    failed customer links, auto-redistribute jobs when a tech calls in sick.
28. **Predictive cash flow** — 90-day forecast that prescribes actions
    ("send tune-up campaign to 200 customers, expect $40K"). Builds on: predictive
    revenue engine, price intelligence.
29. **Compliance autopilot** — tech licenses, insurance certs, 1099s tracked,
    reminded, filed automatically.
30. **Autonomous reputation** — AI-drafted responses to every review in minutes;
    unhappy customers intercepted with a fix before posting publicly.

## Bookkeeping-side A++ candidates (sister app)

Some endgame ideas port directly to the bookkeeping app:
- **Predictive cash flow for the books** — same engine, pointed at the ledger.
- **AI collections** — the bookkeeping app already schedules collections;
  upgrade to AI-driven empathetic follow-up sequences.
- **Compliance autopilot (tax)** — quarterly estimates, 1099 filings, deadline
  tracking — the tax features it already has, made fully autonomous.
- **Anomaly detection** — AI watches every transaction and flags "this expense is
  3× normal" or "duplicate charge detected" without being asked.
- **Autonomous categorization** — every transaction categorized at confidence;
  only low-confidence items ever surface for human review.

## Principles

- One feature per checkpoint: build → test → report → user go-ahead before the next.
- AI-native, not AI-bolted-on: every feature asks "what does the AI version look like?"
- Flat pricing always: never punish the customer for growing (their tech count going
  up should not raise their bill).
- No parallel data models: extend existing collections (appointments, customers,
  quotes/jobs) rather than inventing new ones.
