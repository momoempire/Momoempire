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
3. **GPS tech tracking** — "tech's on the way" live map + ETA texts for customers.
4. **Financing at checkout** — customer financing offers embedded in quotes/invoices.
5. **Bookkeeping link** — sync customers, invoices, payments with our OWN
   bookkeeping app (the QuickBooks competitor), not QuickBooks. Provider-abstracted
   adapter; per-tenant encrypted API token; auto-sync on invoice-paid. (Replaces
   the earlier QuickBooks-sync plan per user directive 2026-10-15.)
6. **AI photo estimates** — customer sends a photo, AI generates a draft estimate
   (leans on the pricebook from #1).

## Phase B — Beyond parity (the distance-makers)

7. **AI dispatcher** — the board assigns itself: skill + location + traffic +
   profitability optimization, human approves. (Builds on dispatch board.)
8. **AI onboarding** — shop uploads old invoices/customer list; AI builds their
   pricebook, customers, and service history in an afternoon. Kills ServiceTitan's
   implementation-moat advantage and becomes the #1 sales pitch.
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

## Principles

- One feature per checkpoint: build → test → report → user go-ahead before the next.
- AI-native, not AI-bolted-on: every feature asks "what does the AI version look like?"
- Flat pricing always: never punish the customer for growing (their tech count going
  up should not raise their bill).
- No parallel data models: extend existing collections (appointments, customers,
  quotes/jobs) rather than inventing new ones.
