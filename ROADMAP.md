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

## Phase A — ServiceTitan parity gaps (build in order, one per checkpoint)

1. **Pricebook** — flat-rate pricebook: services, parts, labor matrices; versioned;
   feeds quotes/invoices/dispatch with consistent pricing.
2. **Equipment history** — per-customer-location equipment records (make/model/serial,
   install date, service history); adds the missing service-address field to the
   data model (appointments/customers have none today).
3. **GPS tech tracking** — "tech's on the way" live map + ETA texts for customers.
4. **Financing at checkout** — customer financing offers embedded in quotes/invoices.
5. **QuickBooks sync** — two-way sync of customers, invoices, payments.
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
    Launch order: HVAC first (Mind at Ease HVAC is the reference brand), then
    plumbing, electrical. Follow with: roofing, appliance repair, garage doors,
    pest control, lawn/landscaping, pool service, painting, handyman, locksmith,
    septic & well, solar.

## Principles

- One feature per checkpoint: build → test → report → user go-ahead before the next.
- AI-native, not AI-bolted-on: every feature asks "what does the AI version look like?"
- Flat pricing always: never punish the customer for growing (their tech count going
  up should not raise their bill).
- No parallel data models: extend existing collections (appointments, customers,
  quotes/jobs) rather than inventing new ones.
