# Track C — 33 niche packs + landing architecture (owner audit, 2026-10-17)

Branch: `audit/niche-packs-landing` (base: main `bee3e86e`). Scope: item 3 of the
owner directive — verify all 33 niche packs against the directive checklist and
resolve the landing-page architecture question. Existing implementations were
reviewed first; nothing already working was rebuilt.

## Architecture determination (the directive's explicit question)

**One shared landing template, dynamically driven by the packs — not 33 pages.**

Evidence from the codebase as found:

- `frontend/src/pages/IndustryLanding.jsx` is a 7-line wrapper: `Landing` (one
  template) rendered with per-industry copy from `frontend/src/config/industries.js`.
- Routes are *generated*, not hand-written: `App.js` and
  `frontend/src/waitlist/WaitlistApp.jsx` both map `INDUSTRY_SLUGS` to routes
  (`/<slug>` and `/es/<slug>`), and `frontend/site-meta.js` builds the sitemap
  from the same `INDUSTRY_PATHS`. Extending the slug table extends routes +
  sitemap everywhere at once.
- Brand faces are likewise resolved dynamically (`config/brand.js` /
  `backend/brand.py`: `brandForNiche(slug)` → "Mind At Ease Solutions \<Label\>"
  at `https://<slug>.mindateasesolutions.com`), with the generic parent face
  (PR #96) as the no-niche/unknown default.

The gap was data, not architecture: `INDUSTRY_SLUGS` and `NICHE_LABELS` listed
only **5** industries (hvac, plumbing, electrical, roofing, landscaping), so only
5 of the 33 packs were reachable on the landing surface.

## What was implemented on this branch

1. `frontend/src/config/industries.js` — the slug table now covers all 33 trade
   packs. Slug = pack id hyphenated (`appliance_repair` → `appliance-repair`),
   matching the brand subdomain convention; a new `INDUSTRY_PACK_IDS` map ties
   each slug to its backend pack file. EN labels are the packs' own `trade`
   names; ES labels follow the company's Spanish trade names
   (`backend/locales/es_mx_trades.yaml`) for the 28 added packs, while the
   original five keep their reviewed copy byte-for-byte. Copy stays on the
   existing shared template (early-access claims only, per the file's rule).
   Routes and sitemap pick up all 66 paths (33 × EN/ES) automatically.
2. `frontend/src/config/brand.js` + `backend/brand.py` — `NICHE_LABELS` extended
   5 → 33 in both files (kept in sync, as the files require), so every pack
   resolves its canonical parent-brand face deterministically.
3. `frontend/src/pages/Landing.jsx` — the decorative HVAC icon strip
   (snowflake/fan/thermometer/flame) rendered unconditionally in the shared
   hero, i.e. on *every* industry page. It is now gated to the HVAC industry
   page and the main landing, matching the pre-existing gating of the hero
   badge and `IndustryPoints` glyphs: HVAC imagery appears only on the HVAC
   face; other trades' pages stay trade-neutral.
4. Tests (see "Tests" below), including a new backend depth-guard suite that
   pins the audit's verified floors so a future pack edit can't silently
   hollow out an industry.

Unknown niches are unchanged by design: `industryPage()` returns `null` for
non-pack slugs (verified for `dental`, `legal`, `salon` — marketing-card names
on the main landing, *not* packs — plus garbage input), no route is generated
for them, and brand resolution falls back to the generic **Mind At Ease
Solutions** face. The parent brand is the name on every face; no page carries
any other company name.

## Pack verification method

Every pack was loaded through the repo's own validator
(`backend/trade_packs/__init__.py::validate_pack` — all 33 pass) and then
scored for *depth beyond the schema minimums* against the directive's nine
checklist columns (see matrix). Headline result: **no Missing cells anywhere**;
the packs are content-complete. Observed floors across all 33 packs: pricebook
≥10 items (all with cost + duration + description), appointment types ≥4,
triage ≥4 questions, equipment types ≥3, automation templates ≥3 (≥3 steps
each), review templates ≥3 covering SMS **and** email, receptionist scripts
≥1,607 chars with greeting/booking/emergency/after-hours sections, taglines
unique across all 33, and no two packs sharing an identical script.

### Findings behind the Partial grades

- **Terminology — 14 Partial.** All packs carry the full 7-term set, but
  14 packs' worker noun is a shared generic family ("Technician" ×9 packs,
  "Installer" ×5) rather than a trade-specific noun (Plumber, Electrician,
  Barber, …). Valid, but not industry-distinct. (Content polish, owner call —
  not changed here.)
- **Colors / icons / imagery — 33 Partial (by design).** Every pack defines a
  distinct `colors.accent`, but the pack schema has *no* icon or imagery
  field: brand faces return `glyph: null` for trades and the landing chrome
  uses a neutral Sparkles glyph (HVAC-only iconography aside). There is no
  per-pack imagery asset anywhere in the product. Closing this needs a schema
  + design decision (add `icon`/`image` to the pack schema), not a data fix.
- **Pricebook — 14 Partial.** All items in all packs carry price, cost,
  duration and description; the Partials use only 2 of the 4 schema
  categories (service+addon or service+part — no labor/part lines). Plausibly
  natural for service-only trades; flagged for owner awareness, not changed.

### es-MX spot check (translation wiring, PRs #90–#92)

- `backend/locales/es_mx_trades.yaml` covers **exactly** the 33 pack ids (no
  gaps, no extras); it is loaded by `locales.es_mx_trade_overlay()` and
  consumed by `market_packs/resolver.py` for the trade×market face — wired,
  not a dead file. Existing test `test_overlay_covers_exactly_the_33_trade_packs`
  pins the id set.
- Sampled packs resolve in both languages: HVAC (`aire acondicionado y
  calefacción` / `técnico de clima`, minisplit note), plumbing (`plomería` /
  `plomero`, peninsular `fontanería` on the avoid list), pest control
  (`control de plagas y fumigación` / `fumigador`), real estate
  (`bienes raíces`), barbershop (`barbería`). Landing copy resolves EN + ES for
  all 33 slugs (new `industries.test.js` iterates every slug × language).

## Books taxonomy cross-check (read-only — ccempireproject100K/Bookkeeping-start-add-on @ main 653e49b)

Both products carry **33** niches and share one taxonomy for **31 of 33** ids.
Two id drifts (same trades, different identifiers):

| Trade | Momo pack id | Books niche id |
|---|---|---|
| #27 whole-home generators | `generators` | `whole_home_generators` |
| #24 fencing & concrete | `fencing_concrete` | `fencing` |

Reported only — no books files were touched from this track. A rename on
either side (or an explicit alias map in the skin resolver) is an owner
decision; tenant records keyed by id make a silent rename unsafe.

## Tests

- Backend targeted (trade packs, brand config, MX localization, market packs,
  new audit guards): **149 passed** (142 baseline + 7 new/extended).
- Backend full suite: baseline (base commit) 1,524 passed / 42 failed /
  138 errors / 124 skipped → after this change **1,528 passed** / 42 failed /
  138 errors / 124 skipped, with the FAILED/ERROR test-name sets
  **byte-identical** before and after (the +4 passes are exactly the new
  Track C tests; the 42/138 are the known sandbox environmental set).
- Frontend changed-area suites (industries, IndustryLanding, brandConfig,
  brandLeak, siteMeta, all Waitlist suites): all pass except the pre-existing
  environmental `brandLeak` EISDIR failure, which fails identically on the
  base commit. Full frontend suite: failing-test name set **identical**
  before/after this change (37 names, all pre-existing, in unrelated
  estimator/estimate/sleep-check/theme suites + the two environmental ones).
- `IndustryLanding.test.js` iterates `INDUSTRY_SLUGS`, so the render suite now
  covers all 33 industries × EN/ES automatically (was 5 × 2).

## Classification (per directive)

- **Confirmed Fixed:** landing reachability — only 5 of 33 packs had landing
  routes/copy/brand labels; now all 33 resolve via the shared dynamic
  template (EN + ES), with HVAC icon-strip leakage onto other trades' pages
  removed.
- **Confirmed Broken:** none in pack content. (The 5-of-33 landing gap above
  was the one confirmed functional defect in scope.)
- **Needs Testing:** the 28 new Spanish landing labels are draft copy from
  the es-MX overlay — they render and resolve, but have not had the human
  accounting/marketing review the original five received; the main landing
  hero still wears the HVAC icon strip by long-standing design (left as-is —
  see below).
- **Requires Owner Decision:** (1) pack icon/imagery schema (the
  Colors/icons/imagery Partial); (2) generic-vs-trade-specific worker nouns
  for the 14 terminology Partial packs; (3) books↔Momo id drift
  (`generators`/`whole_home_generators`, `fencing_concrete`/`fencing`) —
  rename or alias, and on which side; (4) whether the main (generic)
  landing's hero keeps its HVAC icon strip — it is the flagship face's
  established design and the PR #96 guards define the generic surface as
  brand-config strings (which pass), so it was not changed unilaterally.

---
# Track C — 33 niche packs × directive checklist matrix

Grades: Complete = present with production depth · Partial = present but shallow or missing a sub-element · Missing = absent/fails.
Validator: every pack passes backend/trade_packs validate_pack (schema minimums) — grades below measure depth beyond the minimums.

| Pack | Branding & description | Terminology | Colors / icons / imagery | Service catalog & pricebook | Appointment & scheduling | Receptionist greeting & script | Lead qualification & intake | Industry workflows | Automation & review templates |
|---|---|---|---|---|---|---|---|---|---|
| appliance_repair | C | P | P | C | C | C | C | C | C |
| auto_repair | C | P | P | P | C | C | C | C | C |
| barbershop | C | C | P | C | C | C | C | C | C |
| cleaning | C | C | P | P | C | C | C | C | C |
| decks_outdoor | C | P | P | P | C | C | C | C | C |
| electrical | C | C | P | C | C | C | C | C | C |
| fencing_concrete | C | P | P | P | C | C | C | C | C |
| flooring | C | C | P | C | C | C | C | C | C |
| foundation_repair | C | C | P | P | C | C | C | C | C |
| freelancer | C | C | P | C | C | C | C | C | C |
| garage_door | C | P | P | C | C | C | C | C | C |
| generators | C | P | P | C | C | C | C | C | C |
| handyman | C | C | P | C | C | C | C | C | C |
| home_inspection | C | C | P | P | C | C | C | C | C |
| hvac | C | P | P | C | C | C | C | C | C |
| irrigation | C | P | P | P | C | C | C | C | C |
| junk_removal | C | C | P | C | C | C | C | C | C |
| kitchen_bath | C | C | P | C | C | C | C | C | C |
| landscaping | C | C | P | C | C | C | C | C | C |
| locksmith | C | C | P | P | C | C | C | C | C |
| painting | C | C | P | C | C | C | C | C | C |
| pest_control | C | P | P | P | C | C | C | C | C |
| plumbing | C | C | P | C | C | C | C | C | C |
| pool_service | C | P | P | C | C | C | C | C | C |
| real_estate | C | C | P | P | C | C | C | C | C |
| restoration | C | P | P | P | C | C | C | C | C |
| roofing | C | C | P | C | C | C | C | C | C |
| septic_well | C | C | P | C | C | C | C | C | C |
| siding_gutters | C | P | P | P | C | C | C | C | C |
| smart_home | C | P | P | P | C | C | C | C | C |
| solar | C | P | P | C | C | C | C | C | C |
| tree_service | C | C | P | P | C | C | C | C | C |
| windows_doors | C | C | P | C | C | C | C | C | C |

## Column totals (of 33)
- Branding & description: Complete 33, Partial 0, Missing 0
- Terminology: Complete 19, Partial 14, Missing 0
- Colors / icons / imagery: Complete 0, Partial 33, Missing 0
- Service catalog & pricebook: Complete 19, Partial 14, Missing 0
- Appointment & scheduling: Complete 33, Partial 0, Missing 0
- Receptionist greeting & script: Complete 33, Partial 0, Missing 0
- Lead qualification & intake (triage): Complete 33, Partial 0, Missing 0
- Industry workflows (equipment): Complete 33, Partial 0, Missing 0
- Automation & review templates: Complete 33, Partial 0, Missing 0

## Per-pack evidence
### appliance_repair — Appliance Repair (validator: PASS)
- **Branding & description: Complete** — trade='Appliance Repair' tagline='Your AI office, preloaded for Appliance Repair'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#7B4FD6; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 11 items, categories=['addon', 'part', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 4 types, 4 with duration+description: Diagnostic Visit, Repair Visit, Dryer Vent Cleaning, Multi-Appliance Checkup
- **Receptionist greeting & script: Complete** — 2753 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: fridge_cooling, washer_symptom, dryer_heat, appliance_age
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Refrigerator, Washer, Dryer, Range / Oven, Dishwasher
- **Automation & review templates: Complete** — 3 automations (steps [4, 3, 3]), 3 review templates channels=['email', 'sms']

### auto_repair — Auto Repair (validator: PASS)
- **Branding & description: Complete** — trade='Auto Repair' tagline='Your AI office, preloaded for Auto Repair'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#DC2626; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Diagnostic Appointment, Maintenance Visit, Repair Appointment, Drop-Off (all day), State Inspection
- **Receptionist greeting & script: Complete** — 2054 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: auto_vehicle_info, auto_symptom, auto_drivability, auto_warning_lights, auto_history
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Vehicle Lift, Diagnostic Scanner, Alignment Rack, Tire Machine & Balancer, AC Recovery Machine
- **Automation & review templates: Complete** — 4 automations (steps [4, 4, 4, 4]), 3 review templates channels=['email', 'sms']

### barbershop — Barbershop (validator: PASS)
- **Branding & description: Complete** — trade='Barbershop' tagline='Your AI office, preloaded for Barbershops'
- **Terminology: Complete** — terms={"worker": "Barber/Stylist", "worker_plural": "Barbers/Stylists", "shop": "barbershop", "job": "appointment", "job_plural": "appointments", "customer": "client", "customer_plural": "clients"}
- **Colors / icons / imagery: Partial** — accent=#C8102E; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 6 types, 6 with duration+description: Haircut, Skin Fade, Beard Trim, Hot Towel Shave, Consultation, Combo Service
- **Receptionist greeting & script: Complete** — 1607 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: cut_type, client_status, hair_length, addons, timing
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Barber Chair, Clipper & Trimmer Set, Hot Towel Warmer, Station Tools & Mirror, Shampoo Backwash Unit, Waiting Area Seating
- **Automation & review templates: Complete** — 4 automations (steps [4, 3, 3, 3]), 3 review templates channels=['email', 'sms']

### cleaning — Cleaning (validator: PASS)
- **Branding & description: Complete** — trade='Cleaning' tagline='Your AI office, preloaded for Cleaning'
- **Terminology: Complete** — terms={"worker": "Cleaner", "worker_plural": "Cleaners", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#14B8A6; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['addon', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: First-Time Deep Clean, Recurring Home Clean, Move-Out Clean, Office Clean, Walkthrough / Quote
- **Receptionist greeting & script: Complete** — 1844 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: cleaning_service_type, cleaning_home_size, cleaning_frequency, cleaning_addons, cleaning_access_pets
- **Industry workflows (equipment): Complete** — 4 equipment types, 4 with >=3 tracked fields: Vacuum, Cleaning Caddy / Kit, Steam Mop / Floor Machine, Company Vehicle
- **Automation & review templates: Complete** — 4 automations (steps [4, 3, 4, 3]), 3 review templates channels=['email', 'sms']

### decks_outdoor — Decks & Outdoor Living (validator: PASS)
- **Branding & description: Complete** — trade='Decks & Outdoor Living' tagline='Your AI office, preloaded for Decks & Outdoor Living'
- **Terminology: Partial** — terms={"worker": "Installer", "worker_plural": "Installers", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#92400E; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 13 items, categories=['addon', 'service'], 13/13 with cost+duration+description
- **Appointment & scheduling: Complete** — 4 types, 4 with duration+description: Design Consultation, Build Kickoff, Build Day, Final Walkthrough
- **Receptionist greeting & script: Complete** — 1896 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: project_size, material_preference, permits_hoa, timeline
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Lumber Inventory, Composite Board Stock, Power Tool Sets, Footing & Concrete Supplies, Material Delivery Trailer
- **Automation & review templates: Complete** — 3 automations (steps [5, 5, 4]), 3 review templates channels=['email', 'sms']

### electrical — Electrical (validator: PASS)
- **Branding & description: Complete** — trade='Electrical' tagline='Your AI office, preloaded for Electrical'
- **Terminology: Complete** — terms={"worker": "Electrician", "worker_plural": "Electricians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#F5A623; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'labor', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Troubleshooting Visit, Panel Upgrade Estimate, Installation Visit, Safety Inspection, Generator Maintenance
- **Receptionist greeting & script: Complete** — 2917 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: outage_scope, breaker_behavior, safety_hazard, panel_capacity
- **Industry workflows (equipment): Complete** — 4 equipment types, 4 with >=3 tracked fields: Electrical Panel, EV Charger, Generator (Standby), Smoke / CO Detectors
- **Automation & review templates: Complete** — 4 automations (steps [4, 3, 3, 4]), 3 review templates channels=['email', 'sms']

### fencing_concrete — Fencing & Concrete (validator: PASS)
- **Branding & description: Complete** — trade='Fencing & Concrete' tagline='Your AI office, preloaded for Fencing & Concrete'
- **Terminology: Partial** — terms={"worker": "Installer", "worker_plural": "Installers", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#78716C; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['addon', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Free Estimate, Fence Install Day, Concrete Pour Day, Repair Visit, Final Walkthrough
- **Receptionist greeting & script: Complete** — 1952 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: fencing_project_type, fencing_material, fencing_measurements, fencing_hoa_utilities, fencing_timeline
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Post Hole Auger, Concrete Mixer, Plate Compactor, Laser Level / Transit, Company Truck & Trailer
- **Automation & review templates: Complete** — 4 automations (steps [4, 4, 4, 3]), 3 review templates channels=['email', 'sms']

### flooring — Flooring (validator: PASS)
- **Branding & description: Complete** — trade='Flooring' tagline='Your AI office, preloaded for Flooring pros'
- **Terminology: Complete** — terms={"worker": "Installer", "worker_plural": "Installers", "shop": "business", "job": "project", "job_plural": "projects", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#92400E; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'labor', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: In-Home Estimate, Material Selection, Installation Day, Refinish Day, Repair Visit
- **Receptionist greeting & script: Complete** — 2083 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: square_footage, material_preference, subfloor_condition, timeline, furniture
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Tile Wet Saw, Floor Sander, Nail Gun & Compressor Set, Moisture Meter, Transition & Trim Inventory, Delivery Truck
- **Automation & review templates: Complete** — 4 automations (steps [5, 4, 4, 4]), 3 review templates channels=['email', 'sms']

### foundation_repair — Foundation Repair (validator: PASS)
- **Branding & description: Complete** — trade='Foundation Repair' tagline='Your AI office, preloaded for Foundation Repair'
- **Terminology: Complete** — terms={"worker": "Specialist", "worker_plural": "Specialists", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#7C4A21; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 13 items, categories=['addon', 'service'], 13/13 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Foundation Assessment, Pre-Repair Consultation, Pier Installation Day, Waterproofing Project Day, Follow-up Crack Check
- **Receptionist greeting & script: Complete** — 2203 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: crack_profile, settlement_signs, water_intrusion, repair_timeline
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Pier Driver Rig, Mini Excavator, Steel Pier Inventory, Inspection Kit, Carbon Fiber Strap Kits
- **Automation & review templates: Complete** — 4 automations (steps [5, 5, 3, 3]), 3 review templates channels=['email', 'sms']

### freelancer — Freelancer (validator: PASS)
- **Branding & description: Complete** — trade='Freelancer' tagline='Your AI office, preloaded for Freelancers'
- **Terminology: Complete** — terms={"worker": "Freelancer", "worker_plural": "Freelancers", "shop": "business", "job": "project", "job_plural": "projects", "customer": "client", "customer_plural": "clients"}
- **Colors / icons / imagery: Partial** — accent=#7C3AED; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 11 items, categories=['addon', 'labor', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 6 types, 6 with duration+description: Discovery Call, Project Kickoff, Design / Draft Review, Retainer Check-In, Final Handoff, Rush Consult
- **Receptionist greeting & script: Complete** — 1694 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: project_type, budget_range, deadline, deliverables, decision_maker
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Software Seat, Workstation / Laptop, Active Contract, Retainer Slot, Portfolio Asset, Reference Library
- **Automation & review templates: Complete** — 4 automations (steps [5, 4, 4, 4]), 3 review templates channels=['email', 'sms']

### garage_door — Garage Door (validator: PASS)
- **Branding & description: Complete** — trade='Garage Door' tagline='Your AI office, preloaded for Garage Door'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#2F6B4F; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 11 items, categories=['addon', 'part', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Diagnostic Visit, Spring Replacement, Opener Installation, New Door Estimate, Tune-Up Visit
- **Receptionist greeting & script: Complete** — 2935 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: spring_check, door_stuck_position, opener_vs_door, sensor_check
- **Industry workflows (equipment): Complete** — 3 equipment types, 3 with >=3 tracked fields: Garage Door, Garage Door Opener, Commercial Overhead Door
- **Automation & review templates: Complete** — 4 automations (steps [4, 3, 3, 3]), 3 review templates channels=['email', 'sms']

### generators — Whole-Home Generators (validator: PASS)
- **Branding & description: Complete** — trade='Whole-Home Generators' tagline='Your AI office, preloaded for Whole-Home Generators'
- **Terminology: Partial** — terms={"worker": "Installer", "worker_plural": "Installers", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#D97706; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 13 items, categories=['addon', 'part', 'service'], 13/13 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Site Assessment, Installation Day, Annual Maintenance Visit, Storm-Season Tune-Up, Repair / No-Start Call
- **Receptionist greeting & script: Complete** — 2119 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: outage_profile, home_load, fuel_source, switch_status
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Generator Inventory, Transfer Switch Inventory, Service Trucks, Load Test Bank, Crane / Placement Lift
- **Automation & review templates: Complete** — 4 automations (steps [5, 5, 4, 4]), 3 review templates channels=['email', 'sms']

### handyman — Handyman (validator: PASS)
- **Branding & description: Complete** — trade='Handyman' tagline='Your AI office, preloaded for Handyman'
- **Terminology: Complete** — terms={"worker": "Handyman", "worker_plural": "Handymen", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#F59E0B; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Punch List Estimate, Half-Day Visit, Full-Day Visit, Single Task Visit, Recurring Property Visit
- **Receptionist greeting & script: Complete** — 1824 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: handyman_task_list, handyman_trade_scope, handyman_materials, handyman_access, handyman_timeline
- **Industry workflows (equipment): Complete** — 4 equipment types, 4 with >=3 tracked fields: Power Tool Kit, Ladder, Stud Finder / Diagnostic Kit, Company Vehicle
- **Automation & review templates: Complete** — 4 automations (steps [4, 4, 3, 3]), 3 review templates channels=['email', 'sms']

### home_inspection — Home Inspection (validator: PASS)
- **Branding & description: Complete** — trade='Home Inspection' tagline='Your AI office, preloaded for Home Inspection'
- **Terminology: Complete** — terms={"worker": "Inspector", "worker_plural": "Inspectors", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#EA580C; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 13 items, categories=['addon', 'service'], 13/13 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Buyer Home Inspection, Pre-Listing Inspection, Radon Test Drop-off, Radon Test Pickup, Re-Inspection
- **Receptionist greeting & script: Complete** — 2328 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: contingency_deadline, home_profile, addon_needs, client_role
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Inspection Tool Kits, Radon Monitors, Sewer Cameras, Inspection Drones, Inspection Vehicles
- **Automation & review templates: Complete** — 4 automations (steps [5, 4, 4, 3]), 3 review templates channels=['email', 'sms']

### hvac — HVAC (validator: PASS)
- **Branding & description: Complete** — trade='HVAC' tagline='Your AI office, preloaded for HVAC'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#1E6FB8; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'labor', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Diagnostic Visit, Tune-Up / Maintenance, System Replacement Estimate, Repair Visit, Indoor Air Quality Consult
- **Receptionist greeting & script: Complete** — 2885 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: no_heat_no_cool, airflow_check, refrigerant_symptoms, system_age
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Split System Air Conditioner, Gas Furnace, Heat Pump, Thermostat, Air Handler / Evaporator Coil
- **Automation & review templates: Complete** — 4 automations (steps [5, 4, 4, 3]), 3 review templates channels=['email', 'sms']

### irrigation — Irrigation (validator: PASS)
- **Branding & description: Complete** — trade='Irrigation' tagline='Your AI office, preloaded for Irrigation'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#0284C7; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['addon', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: System Checkup, Spring Start-Up Visit, Winterization Visit, Repair Call, New System Consult
- **Receptionist greeting & script: Complete** — 2083 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: zone_count, coverage_issue, controller_type, water_source
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Sprinkler Head Inventory, Controller Models, Pipe & Fitting Stock, Trenchers, Backflow Test Gauges
- **Automation & review templates: Complete** — 3 automations (steps [5, 4, 4]), 3 review templates channels=['email', 'sms']

### junk_removal — Junk Removal (validator: PASS)
- **Branding & description: Complete** — trade='Junk Removal' tagline='Your AI office, preloaded for Junk Removal'
- **Terminology: Complete** — terms={"worker": "Crew Lead", "worker_plural": "Crew Leads", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#16A34A; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'labor', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 4 types, 4 with duration+description: Volume Estimate Visit, Pickup Appointment, Estate Cleanout Day, Commercial Recurring Pickup
- **Receptionist greeting & script: Complete** — 2000 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: volume_estimate, access, hazardous_check, timeline
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Trucks, Trailers, Dumpster Inventory, Hand Tools & Dolly Sets, Disposal & Donation Sites
- **Automation & review templates: Complete** — 3 automations (steps [5, 5, 4]), 3 review templates channels=['email', 'sms']

### kitchen_bath — Kitchen & Bath Remodeling (validator: PASS)
- **Branding & description: Complete** — trade='Kitchen & Bath Remodeling' tagline='Your AI office, preloaded for Kitchen & Bath remodelers'
- **Terminology: Complete** — terms={"worker": "Designer", "worker_plural": "Designers", "shop": "business", "job": "project", "job_plural": "projects", "customer": "client", "customer_plural": "clients"}
- **Colors / icons / imagery: Partial** — accent=#0E7490; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 11 items, categories=['addon', 'labor', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 6 types, 6 with duration+description: Design Consultation, Showroom Visit, In-Home Measure, Material Selection, Progress Walkthrough, Final Walkthrough
- **Receptionist greeting & script: Complete** — 2075 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: remodel_scope, budget_range, timeline, financing, decision_process
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Design Software Seat, Tile Wet Saw, Cabinet Install Jig Set, Dust Control System, Showroom Display, Project Truck / Trailer
- **Automation & review templates: Complete** — 4 automations (steps [5, 4, 4, 4]), 3 review templates channels=['email', 'sms']

### landscaping — Landscaping (validator: PASS)
- **Branding & description: Complete** — trade='Landscaping' tagline='Your AI office, preloaded for Landscaping'
- **Terminology: Complete** — terms={"worker": "Crew Lead", "worker_plural": "Crew", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#3E8E41; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 11 items, categories=['addon', 'labor', 'part', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Recurring Mowing, Cleanup Visit, Install / Project Day, Irrigation Service, Design Consultation
- **Receptionist greeting & script: Complete** — 2605 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: service_type, lot_size, irrigation_symptom, yard_condition
- **Industry workflows (equipment): Complete** — 4 equipment types, 4 with >=3 tracked fields: Lawn Profile, Irrigation System, Landscape Beds, Trees
- **Automation & review templates: Complete** — 4 automations (steps [5, 3, 3, 3]), 3 review templates channels=['email', 'sms']

### locksmith — Locksmith (validator: PASS)
- **Branding & description: Complete** — trade='Locksmith' tagline='Your AI office, preloaded for Locksmiths'
- **Terminology: Complete** — terms={"worker": "Locksmith", "worker_plural": "Locksmiths", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#B45309; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Emergency Lockout, Rekey Service, Lock Installation, Commercial Security Survey, Key / Fob Replacement
- **Receptionist greeting & script: Complete** — 2051 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: service_type, property_type, urgency, lock_info, key_status
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Key Cutting Machine, Pinning / Rekey Kit, Transponder Programmer, Service Van, Safe Opening Kit, Lock Inventory Bin
- **Automation & review templates: Complete** — 4 automations (steps [5, 4, 3, 3]), 3 review templates channels=['email', 'sms']

### painting — Painting (validator: PASS)
- **Branding & description: Complete** — trade='Painting' tagline='Your AI office, preloaded for Painting'
- **Terminology: Complete** — terms={"worker": "Painter", "worker_plural": "Painters", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#3B82F6; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Free Estimate, Interior Room Job, Full-Day Crew Visit, Color Consultation, Touch-Up / Warranty Visit
- **Receptionist greeting & script: Complete** — 1916 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: painting_interior_or_exterior, painting_scope, painting_surface_condition, painting_timeline, painting_color_ready
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Airless Paint Sprayer, Ladder / Scaffold, Paint Booth / Drying Rack, Pressure Washer, Company Vehicle
- **Automation & review templates: Complete** — 4 automations (steps [4, 3, 3, 4]), 3 review templates channels=['email', 'sms']

### pest_control — Pest Control (validator: PASS)
- **Branding & description: Complete** — trade='Pest Control' tagline='Your AI office, preloaded for Pest Control'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#4C8C2B; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 11 items, categories=['addon', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Initial Inspection, One-Time Treatment, Quarterly Service, WDI / Termite Inspection, Wildlife / Exclusion Job
- **Receptionist greeting & script: Complete** — 2906 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: pest_id, infestation_level, bed_bug_confirm, termite_signs
- **Industry workflows (equipment): Complete** — 4 equipment types, 4 with >=3 tracked fields: Bait Stations (Termite), Rodent Stations, Mosquito System, Property Profile
- **Automation & review templates: Complete** — 4 automations (steps [4, 3, 3, 3]), 3 review templates channels=['email', 'sms']

### plumbing — Plumbing (validator: PASS)
- **Branding & description: Complete** — trade='Plumbing' tagline='Your AI office, preloaded for Plumbing'
- **Terminology: Complete** — terms={"worker": "Plumber", "worker_plural": "Plumbers", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#0E7C86; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'labor', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Diagnostic Visit, Drain Service, Water Heater Replacement, Fixture Installation, Repipe / Remodel Estimate
- **Receptionist greeting & script: Complete** — 2792 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: leak_severity, clog_scope, water_heater_status, water_pressure
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Water Heater (Tank), Tankless Water Heater, Sewer Line, Water Softener / Filtration, Sump Pump
- **Automation & review templates: Complete** — 4 automations (steps [5, 3, 3, 3]), 3 review templates channels=['email', 'sms']

### pool_service — Pool Service (validator: PASS)
- **Branding & description: Complete** — trade='Pool Service' tagline='Your AI office, preloaded for Pool Service'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#1B9AAA; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'labor', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Weekly Service Stop, Green Pool Cleanup, Equipment Repair, Opening / Closing, Leak Detection
- **Receptionist greeting & script: Complete** — 2982 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: water_clarity, equipment_status, water_loss, service_interest
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Pool, Pump, Filter, Heater, Automation / Salt System
- **Automation & review templates: Complete** — 4 automations (steps [5, 3, 3, 3]), 3 review templates channels=['email', 'sms']

### real_estate — Real Estate (validator: PASS)
- **Branding & description: Complete** — trade='Real Estate' tagline='Your AI office, preloaded for Real Estate teams'
- **Terminology: Complete** — terms={"worker": "Agent", "worker_plural": "Agents", "shop": "agency", "job": "listing", "job_plural": "listings", "customer": "client", "customer_plural": "clients"}
- **Colors / icons / imagery: Partial** — accent=#1E40AF; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 10 items, categories=['addon', 'service'], 10/10 with cost+duration+description
- **Appointment & scheduling: Complete** — 6 types, 6 with duration+description: Buyer Consultation, Listing Appointment, Home Showing, Open House, Offer Review / Negotiation, Closing Appointment
- **Receptionist greeting & script: Complete** — 1893 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: intent, timeline, pre_approval, price_range, property_details
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Active Listing, CRM Seat, Showing Vehicle, Lockbox Inventory, Photo/Video Kit, Signage Set
- **Automation & review templates: Complete** — 4 automations (steps [5, 5, 4, 4]), 3 review templates channels=['email', 'sms']

### restoration — Restoration (validator: PASS)
- **Branding & description: Complete** — trade='Restoration' tagline='Your AI office, preloaded for Restoration'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#0EA5E9; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['addon', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Emergency Dispatch, Damage Assessment, Mitigation Day, Monitoring Visit, Final Walkthrough
- **Receptionist greeting & script: Complete** — 2372 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: restoration_damage_type, restoration_active, restoration_safety, restoration_insurance, restoration_affected_areas
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Dehumidifier (LGR), Air Mover, HEPA Air Scrubber, Moisture Meter / Thermal Camera, Ozone / Hydroxyl Generator, Company Vehicle
- **Automation & review templates: Complete** — 4 automations (steps [4, 4, 4, 4]), 3 review templates channels=['email', 'sms']

### roofing — Roofing (validator: PASS)
- **Branding & description: Complete** — trade='Roofing' tagline='Your AI office, preloaded for Roofing'
- **Terminology: Complete** — terms={"worker": "Roofer", "worker_plural": "Roofers", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#8B3A2B; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 11 items, categories=['addon', 'labor', 'part', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Roof Inspection, Replacement Estimate, Leak Repair, Storm Damage Assessment, Maintenance Visit
- **Receptionist greeting & script: Complete** — 2797 chars, sections=['greeting', 'booking', 'emergency', 'after-hours', 'after hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: leak_active, storm_damage, roof_age, insurance_claim
- **Industry workflows (equipment): Complete** — 4 equipment types, 4 with >=3 tracked fields: Roof System, Gutters, Skylight, Attic / Ventilation
- **Automation & review templates: Complete** — 4 automations (steps [5, 3, 3, 3]), 3 review templates channels=['email', 'sms']

### septic_well — Septic & Well (validator: PASS)
- **Branding & description: Complete** — trade='Septic & Well' tagline='Your AI office, preloaded for Septic & Well pros'
- **Terminology: Complete** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "business", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#15803D; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Septic Pump-Out, Septic Inspection, Well Service Call, Water Sample Pickup, Repair Estimate Visit
- **Receptionist greeting & script: Complete** — 1956 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: issue_type, last_pump_date, household_load, water_source, access
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Pump Truck, Jetting Machine, Inspection Camera, Mini Excavator, Pump Hoist Rig, Locating Equipment
- **Automation & review templates: Complete** — 4 automations (steps [4, 5, 4, 4]), 3 review templates channels=['email', 'sms']

### siding_gutters — Siding & Gutters (validator: PASS)
- **Branding & description: Complete** — trade='Siding & Gutters' tagline='Your AI office, preloaded for Siding & Gutters'
- **Terminology: Partial** — terms={"worker": "Installer", "worker_plural": "Installers", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#64748B; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['addon', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 4 types, 4 with duration+description: Exterior Assessment, Storm Damage Inspection, Install Day, Final Walkthrough
- **Receptionist greeting & script: Complete** — 2069 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: material_interest, storm_damage, linear_footage, claim_status
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Siding Inventory, Gutter Coil Stock, Seamless Gutter Machine, Scaffold & Ladder Sets, Storm Damage Photo Kits
- **Automation & review templates: Complete** — 3 automations (steps [5, 5, 5]), 3 review templates channels=['email', 'sms']

### smart_home — Smart Home & Security (validator: PASS)
- **Branding & description: Complete** — trade='Smart Home & Security' tagline='Your AI office, preloaded for Smart Home & Security'
- **Terminology: Partial** — terms={"worker": "Technician", "worker_plural": "Technicians", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#0EA5E9; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['addon', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 4 types, 4 with duration+description: In-Home Consultation, Install Appointment, System Walkthrough, Troubleshooting Call
- **Receptionist greeting & script: Complete** — 2118 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 4 questions, 4 with >=3 options + route: device_goals, ecosystem, security_goals, connectivity
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Camera Inventory, Control Panels, Smart Lock Inventory, Sensor Inventory, NVR / DVR Units
- **Automation & review templates: Complete** — 3 automations (steps [5, 4, 4]), 3 review templates channels=['email', 'sms']

### solar — Solar (validator: PASS)
- **Branding & description: Complete** — trade='Solar' tagline='Your AI office, preloaded for Solar'
- **Terminology: Partial** — terms={"worker": "Installer", "worker_plural": "Installers", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"} (worker noun is a shared generic family — 'Technician'/'Installer' set reused across sibling packs — not a trade-specific noun like Plumber/Electrician/Barber)
- **Colors / icons / imagery: Partial** — accent=#F97316; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 11 items, categories=['addon', 'part', 'service'], 11/11 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Free Site Assessment, Design Review Call, Installation Day, Inspection Walkthrough, Monitoring & Handoff Visit
- **Receptionist greeting & script: Complete** — 2050 chars, sections=['greeting', 'booking', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: solar_new_or_existing, solar_homeownership, solar_bill, solar_roof, solar_issue
- **Industry workflows (equipment): Complete** — 5 equipment types, 5 with >=3 tracked fields: Solar Panel Pallet, Inverter, Battery Unit, Racking / Mounting Hardware, Company Vehicle
- **Automation & review templates: Complete** — 4 automations (steps [3, 3, 4, 3]), 3 review templates channels=['email', 'sms']

### tree_service — Tree Service (validator: PASS)
- **Branding & description: Complete** — trade='Tree Service' tagline='Your AI office, preloaded for Tree Service'
- **Terminology: Complete** — terms={"worker": "Arborist", "worker_plural": "Arborists", "shop": "shop", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#16A34A; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Partial** — 12 items, categories=['addon', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 6 types, 6 with duration+description: Free Estimate, Removal Day, Trimming Visit, Stump Grinding Visit, Emergency Response, Health Assessment
- **Receptionist greeting & script: Complete** — 1975 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: tree_work_type, tree_hazard, tree_size_access, tree_count, tree_timeline
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Chainsaw, Stump Grinder, Chipper, Bucket Truck / Lift, Climbing Gear Set, Company Truck & Trailer
- **Automation & review templates: Complete** — 4 automations (steps [4, 4, 3, 3]), 3 review templates channels=['email', 'sms']

### windows_doors — Windows & Doors (validator: PASS)
- **Branding & description: Complete** — trade='Windows & Doors' tagline='Your AI office, preloaded for Window & Door pros'
- **Terminology: Complete** — terms={"worker": "Installer", "worker_plural": "Installers", "shop": "business", "job": "job", "job_plural": "jobs", "customer": "customer", "customer_plural": "customers"}
- **Colors / icons / imagery: Partial** — accent=#0284C7; pack schema has no icon/imagery field (brand face glyph=null; landing uses neutral Sparkles, HVAC-only iconography)
- **Service catalog & pricebook: Complete** — 12 items, categories=['addon', 'part', 'service'], 12/12 with cost+duration+description
- **Appointment & scheduling: Complete** — 5 types, 5 with duration+description: Free In-Home Estimate, Precision Measurement, Installation Day, Service / Repair Visit, Final Inspection
- **Receptionist greeting & script: Complete** — 2097 chars, sections=['greeting', 'booking', 'emergency', 'after-hours']
- **Lead qualification & intake (triage): Complete** — 5 questions, 5 with >=3 options + route: window_count, issue_type, energy_goals, budget_range, home_age
- **Industry workflows (equipment): Complete** — 6 equipment types, 6 with >=3 tracked fields: Installation Truck, Glass Handling Set, Caulking & Sealant Kit, Laser Measuring Kit, Replacement Sash Inventory, Ladder & Scaffolding Set
- **Automation & review templates: Complete** — 4 automations (steps [5, 4, 4, 3]), 3 review templates channels=['email', 'sms']
