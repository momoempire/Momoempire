# Security RE-PROBE — resolution of the three unattributed Track D probes

**Base:** pristine `main` @ `bee3e86e90316d5736332a05a4f4fb246a6be813`
**Branch:** `audit/security-reprobe` · **Tests:** `backend/tests/test_security_reprobe.py` (9 tests, all passing)
**Suite delta:** branch = **1,533 passed / 42 failed / 138 errors / 124 skipped** vs baseline
1,524 / 42 / 138 / 124 — exactly +9 (the new probe tests); failed+error sets identical to baseline.

Three probe test names were observed in a mid-run monitoring snapshot during the
Track D audit but exist in no delivered artifact. Each was re-created adversarially
against pristine main (fake in-memory Mongo + TestClient, PR #101 style). **All three
resolve CONFIRMED SAFE. No code fix was required; this PR is tests + this document only.**

---

## Probe 1 — `test_reviews_list_and_stats_are_tenant_scoped` → ✅ CONFIRMED SAFE

Every reviews read surface was exercised as tenant B against seeded tenant A data:

| Surface | Scoping evidence | Probe result |
|---|---|---|
| `GET /tenants/reviews` (review_requests) — `routers/reviews.py:19` | `find({"tenant_id": user["tenant_id"]})` | B sees only B's request |
| `GET /api/reputation/tenants/reviews` (ingested reviews) — `routers/reputation.py:286` | `find({"tenant_id": user["tenant_id"]})` | count=1, A's review text absent |
| `GET /api/reputation/tenants/overview` (stats) — `routers/reputation.py:172-173,199` | `tenant_id = user["tenant_id"]`; reviews + review_requests both queried by it | B: review_count=1, avg_rating=5.0 (A's two 1★ don't leak in), review_requests_sent=1 |
| `GET /advisor/snapshot` (stats) — `routers/advisor.py:23` | `count()` helper merges `{"tenant_id": tid}` into every query | B: review_responses_30d=1 (A had 2 more) |
| `GET /testimonials` — `routers/testimonials.py:225` | `q = {"tenant_id": user["tenant_id"]}` | B sees only B's testimonial |

Companion checks: cross-tenant mutation `POST /api/reputation/tenants/reviews/{A-id}/draft`
as B → **404** (mutations look up `{"tenant_id", "id"}`, `reputation.py:340/383`).
`GET /public/reviews/{token}` is the customer capability link by design — exact
unguessable token resolves, unknown token 404s, no list/enumeration surface exists.
Other review-count consumers found by sweep (`routers/pipeline.py:28,177`,
`routers/post_job.py:275`) are likewise tenant-scoped, and `routers/inbox.py`
`/ingest/sms` (which carries a TODO about Twilio signatures) is **not** a raw
webhook — it requires an authenticated tenant user (`require_tenant_user`).

## Probe 2 — `test_billing_overage_endpoints_are_admin_gated` → ✅ CONFIRMED SAFE

- `routers/overage.py:133-134` — the whole `admin_router` (`/admin/overage`, incl.
  `POST /run` with `dry_run`/`charge`, `/preview`, `/history`) carries a router-level
  `dependencies=[Depends(require_platform_admin)]`.
- `security.py:149-152` — `require_platform_admin` rejects any role ≠ `platform_admin` (403).
- `server.py:180-181` — tenant and admin routers each mounted exactly once; no
  alternate/ungated mount of the admin router exists.
- Probe results: unauthenticated → **401**; tenant `staff` / `admin` / `owner` → **403**
  on run, preview and history; `platform_admin` → **200**.
- Cron variant: `routers/cron.py:13-20` `_authorized()` fails closed when
  `WEBHOOK_CRON_SECRET` is unset and compares with `hmac.compare_digest`. Probe:
  `POST /cron/overage-nightly` → **401** with secret unset (any bearer), **401** with
  no/wrong bearer when set, **200** `{"accepted": true}` with the correct bearer.
- The tenant surface `GET /usage/overage` computes for the caller's own tenant only
  (`routers/overage.py:127-129`).

## Probe 3 — `test_twilio_webhook_rejects_unsigned_requests_in_production` → ✅ CONFIRMED SAFE

- `routers/twilio_webhook.py:64` — the router carries a router-wide
  `dependencies=[Depends(require_twilio_signature)]` covering voice, voice-turn, SMS,
  message-status, outbound-callback and missed-call; mounted once (`server.py:184`).
- `services/twilio_signature.py:88-111` — `verify()` fails closed everywhere outside
  explicit dev: no auth token configured → **403**; missing signature → **403**;
  invalid signature → **403**; `PUBLIC_BACKEND_URL` unset outside dev → **403** (the
  signed URL is pinned to the configured origin, never to spoofable Host/forwarded
  headers); Twilio SDK missing → **403**. The pass-through applies only when
  `APP_ENV` is explicitly `dev`/`development`/`local` **and** no token exists —
  an *unset* `APP_ENV` is not dev, so a production box that forgot `APP_ENV` still
  fails closed.
- Probe results with `APP_ENV=production`: unsigned POSTs to `/twilio/voice`,
  `/twilio/sms`, `/twilio/message-status` → **403**; wrong signature → **403**;
  a signature valid for a *different* URL (replay) → **403**; no token configured →
  **403** (with `APP_ENV=production` and with `APP_ENV` unset).
- Control: a genuinely signed request (Twilio `RequestValidator` signature over the
  configured public URL + form params) is admitted — **200** TwiML — proving the
  gate is a real signature check, not a blanket block.

---

**Bottom line for the completion report:** the three mid-run probe failures do not
reproduce on pristine main. The reviews reads, the overage admin/cron gates, and
the Twilio webhook signature enforcement are all in place and now pinned by the
regression tests in this PR.
