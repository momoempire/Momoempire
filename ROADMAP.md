# AI Office Platform — Roadmap

> Target: surpass ServiceTitan ($245–500/tech/mo, 6–12 mo implementations) with an
> AI-native platform, flat pricing, and deploy-in-days onboarding.
> Wedge: the small shops ServiceTitan prices out.
>
> **Build order (authorized 2026-10-15):** full roadmap build underway.
> Wave 1: marketing automation (#31) — dual-purpose (shops→homeowners AND
> platform→prospective shops). Then Phase B (7–12), Phase C + gaps (13–18,
> 32–36), Phase D (19–24), Phase E (25–30), differentiators (37–39),
> customer-service excellence initiative. One stacked PR per feature, no merges
> without user review.

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
- **Wave 7 — differentiators** (`wave/differentiators`, PR #84): predictive
  equipment-failure alerts (rule-based risk scoring + feedback loop), AI home
  report after every visit (magic-link homeowner view + send/open tracking),
  transferable home service profile (consent-gated transfer on sale with PII
  redaction). 71/71 tests.
- **Wave 8 — customer-service excellence** (`wave/service-excellence`, PR #85):
  Layer 1 — helpdesk ticketing with SLAs, public status page + proactive
  monitoring, AI support agent + shop health/churn; Layer 2 — omnichannel inbox,
  conversation intelligence + sentiment escalation, proactive outreach +
  missed-call rescue + multilingual. 161/161 tests.
- **Wave 9 — 46 Stripe market packs** (`wave/market-packs`, PR #86): lean
  per-country YAML packs (colors, languages, flag) + trade x market skin
  resolver (`GET /tenants/market-packs/resolve?trade=&market=`) — market colors
  override, trade colors fallback. 59/59 tests.

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
   (google-genai==2.26.0); resolution OPENAI → GEMINI → raise,
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
   its own Twilio subaccount at signup (platform holds one parent account).
   Numbering is flexible per business: port in their existing number(s), take
   freshly provisioned number(s), or both (e.g. keep the familiar main line +
   add a dedicated AI/marketing line). Required for the 270-business
   plan — one shared number cannot serve many businesses (branding, 10DLC
   compliance, throughput, reputation isolation). **Fee model (decided
   2026-10-15): bundled** — telephony included in the flat platform fee with a
   generous usage allowance; overages billed through. Shops never open their own
   Twilio account.
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

## Competitor-gap audit — added 2026-10-15

**Gaps vs ServiceTitan-class (must-add):**
31. **Marketing automation** — email/SMS campaigns, audience segmentation,
    automated nurture sequences. Plugs directly into the lead-gen core of the
    business model. (Phase B priority.)
32. **Tech time tracking / timesheets** — clock-in/out feeding payroll; pairs
    with the bookkeeping app's payroll-lite.
33. **Inventory / truck stock** — what's on each truck and in the warehouse;
    pairs with Phase D parts ordering (buying) by covering tracking.
34. **Multi-location management** — consolidated reporting + cross-location
    dispatch for multi-branch shops. Matters for the 270-business plan.

**Gaps vs the broader field (Jobber, Housecall Pro):**
35. **Public API + Zapier** — integration ecosystem; lets shops connect the
    thousands of tools we don't build. Table stakes for platform credibility.
36. **Custom job forms / checklists** — structured on-site checklists (install
    QC, safety, trade-specific) techs complete from the field.

**New differentiators nobody has:**
37. **Predictive failure alerts** — equipment history + service data →
    "your capacitor shows wear patterns; 73% fail within 90 days — book now."
    Our data moat, weaponized. Surfaced to homeowners via the home hub.
38. **AI home report after every visit** — beautiful homeowner-facing summary:
    what was done, photos, system health scores, what's coming due. Drives
    reviews, repeat business, and home-hub adoption.
39. **Home profile transfer on sale** — when a homeowner sells, the home's full
    service history transfers to the new owner with the shop retained as
    provider. Lock-in through real estate transactions.

## Customer-service excellence initiative (both layers, one brain)

The moat: one AI brain serves the shop AND the shop's customers with shared
context. Nobody else is both the software and the service provider.

**Layer 1 — our support for the shops:**
- AI first responder trained on platform docs; ticket escalation with real SLAs
- Proactive monitoring: we detect issues (e.g. receptionist error-rate spikes)
  and reach out before the shop notices
- Per-shop health scores + churn prediction
- Public status page; white-glove onboarding concierge (first 90 days)

**Layer 2 — us as customer service for the shops' customers:**
- Omnichannel inbox: voice, SMS, web chat, email, FB/IG DMs — AI on every channel
- Real-time sentiment detection → instant human escalation with full transcript
- Conversation intelligence: every call transcribed, scored, summarized into the
  job record
- Proactive outreach: missed appointments, stale quotes — AI reaches out first
- Multilingual voice (EN/ES+); 60-second missed-call rescue (text + callback)

**Shine extras (both audiences):**
- Homeowner home-hub app (Phase D #19)
- Benchmark reports for shops ("you answer 94% of calls vs 78% for shops like
  yours") — feeds the price-intelligence data moat
- Post-job satisfaction pulse feeding tech leaderboards (Phase D #24)
- Dogfooding: our own support line answered by our AI receptionist — the demo
  no competitor can match

**The affordable human layer (model decided 2026-10-15):**
- AI contains 80–90%; humans handle only escalations
- Async-first: chat/tickets + scheduled callbacks — no live phone queues (one
  async agent covers 3–5× the volume of a phone agent)
- **Phase 0 — founder IS customer service.** The goal is revenue before the first
  hire: founder on-call during waking hours; overnight the AI holds customers
  warm ("specialist follows up by 9 AM") and the ticket is waiting at 9. Feels
  24/7 without the founder never sleeping. Viable to ~20 shops.
- **Phase 1 — trusted part-timers, on-call (revisit).** 1–2 known customer-service
  people, staggered coverage windows (e.g. mornings/evenings), 15–30 min SLA on
  escalations; hourly or retainer + per-ticket; founder remains the backup for
  the truly weird stuff. PII guardrails: role-based access, no data exports.
- Phase 2 — first full/part-time support hire at ~20–50 shops; fractional/gig
  overflow after that; done-for-you BPO only if scale ever demands it
- Flywheel: every human-resolved ticket becomes a knowledge-base article the AI
  answers next time — the human layer shrinks per-shop as the AI learns

## Principles

- One feature per checkpoint: build → test → report → user go-ahead before the next.
- AI-native, not AI-bolted-on: every feature asks "what does the AI version look like?"
- Flat pricing always: never punish the customer for growing (their tech count going
  up should not raise their bill).
- No parallel data models: extend existing collections (appointments, customers,
  quotes/jobs) rather than inventing new ones.
