# Merge order and conflict guide (EMP-WL-063)

Applies to the open PRs that target `Cloudflare-2` (base `0256358`). This file only covers
merge order and hand-resolved conflicts. It makes no product decisions. Brann decides what
gets merged, and when.

Checked on 2026-10-08 by merging the whole stack locally in this order (not pushed, not
deployed). Only four merges conflicted (#18, #13, #16, #19), and the five resolutions in
section 3 cover them. They match the four hand resolutions in Watcher's "Final full-stack QC
(waitlist go/no-go)" (verdict GO); see the reconciliation table in section 3. With those applied
(and #26–#36 added as listed in section 1), the backend unit suites passed (324, against a
throwaway local MongoDB), the frontend suites passed (192), and both builds succeeded. The legacy `test_phase*.py`,
`backend_test.py`, `test_call_to_payment.py` and `test_iteration10_*.py` files need a live
server, so they were not part of that run.

## 1. Waitlist launch stack: merge in this order

| Step | PR | Branch | Notes |
|---|---|---|---|
| 1 | #10 | fix/cors-enforce-and-health-leaks | |
| 2 | #8 | fix/admin-password-first-login | |
| 2a | #25 | fix/admin-reset-followups | stacked on #8, merge right after #8 |
| 3 | #11 | fix/waitlist-endpoint-hardening | adds honeypot + Turnstile (see section 4) |
| 4 | #12 | feat/instant-quote-estimator | |
| 5 | #14 | fix/waitlist-followups | |
| 6 | #18 | fix/waitlist-db-errors | **conflict with #10 in `backend/server.py`** (section 3, resolution 1) |
| 7 | #15 | fix/waitlist-confirmation-off | stacked on #14 |
| 8 | #13 | feat/waitlist-only-mode | conflicts in `backend/.env.example` and `Landing.jsx` imports (resolutions 2, 3) |
| 9 | #22 | fix/waitlist-legal-trial-text | after #13 |

After #13, three chains. Each chain has to stay in its own order, but the chains can go in any
order relative to each other:

- **#17, then #24**
- **#16, then #19, then #23**. #16 and #19 conflict in `Landing.jsx` (resolutions 4, 5)
- **#20**, any time after #13

**#21** (waitlist data ops: script + docs) can be merged any time. It touches no files the
others touch.

Follow-up PRs opened after #25. Each one merges right after the PR it is stacked on, and
none of them needs hand resolution (each merges cleanly onto the full stack):

| PR | Stacked on | Merge right after | What |
|---|---|---|---|
| #26 | Cloudflare-2 | any time | this guide (docs only) |
| #27 | #18 | #18 | WL-061 fail fast on a down DB, WL-044 legacy test URLs |
| #28 | #23 | #23 | WL-064/071 landing form error text |
| #29 | #12 | #12 | WL-071/064 estimator error text (same `waitlistErrors.js` as #28, byte-identical) |
| #30 | #22 | #22 | WL-082 fixed "Last updated" date |
| #31 | #20 | #20 | WL-072 canonical + favicon.ico, WL-073 no source maps in the waitlist build |
| #32 | #21 | #21 | WL-074/075 data-ops credentials + typed DELETE confirmation |
| #33 | #27 | #27 | WL-062 same answer for known/new emails when writes fail, WL-044 leftovers |
| #34 | #28 | #28 | WL-093 waitlist bundle has only waitlist strings, WL-094 axe-core devDependency |
| #35 | #30 | #30 | WL-095 privacy TODO slot for Google Fonts and Cloudflare |
| #36 | #24 | #24 | WL-092 deploy doc fixes (Turnstile switches, source maps, site metadata, DB-down/index steps) |
| #37 | Cloudflare-2 | any time (checked clean after #26 on the full stack: 346 backend) | Twilio webhook signatures, not waitlist (EMP-FIX-034) |
| #38 | Cloudflare-2 | any time (checked clean after #37 on the full stack: 384 backend) | CSRF Origin/Referer check for cookie-authenticated writes, not waitlist (EMP-FIX-035) |
| #39 | Cloudflare-2 | any time (checked clean after #38 on the full stack: 392 backend; also clean with #7 and #9) | overdue reminders at most once per invoice per day, blocker 5, not waitlist (EMP-FIX-036) |
| #40 | #7 | #7 (contains #7; updates #7's test fixtures; checked clean on the full stack + #37-#39: 419 backend) | Stripe Connect account/currency/amount checks, platform replay gate, no plan on "unpaid", not waitlist (EMP-FIX-037) |
| #41 | #9 | #9 (contains #9; checked clean on the full stack + #37-#40: 436 backend, 197 Jest, both builds) | quote links: no dead Accept links, expiry enforced, drafts not public, not waitlist (EMP-FIX-038) |

Full order as checked locally: #10, #8, #25, #11, #12, #29, #14, #18, #27, #33, #15, #13, #22,
#30, #35, #17, #24, #36, #16, #19, #23, #28, #34, #20, #31, #21, #32, #26.

Note for #2 (landing-es): #34 keeps a copy of the waitlist strings in
`frontend/src/i18n/locales/waitlist.{en,es}.json`. If #2 changes any `landing.*waitlist*` string,
`WaitlistI18n.test.js` fails until those two files are regenerated from `en.json`/`es.json`.

## 2. PRs outside the waitlist stack (#1–#7, #9)

None of these is needed for the waitlist launch. Hold them until the stack above is merged,
then merge them in any order, except where the notes below say otherwise. Conflicts listed are
against the fully merged stack (`git merge-tree`, 2026-10-08):

| PR | Branch | Conflicts with the merged stack | How to resolve |
|---|---|---|---|
| #1 | feat/emp-dev-002-stripe-checkout | `frontend/src/pages/Pricing.jsx` (from #12/#14) | keep both changes by hand; check that the estimator link from #12 is still there |
| #2 | landing-es | `frontend/src/pages/Landing.jsx` | same rule as #4: keep the honeypot + Turnstile (section 4) |
| #3 | knowledge-embeddings | none | |
| #4 | webrtc-voice-demo | `backend/.env.example`, `backend/routers/marketing.py`, `frontend/src/pages/Landing.jsx` | **section 4 applies**. Keep both sides in `.env.example`. In `marketing.py` keep the stack's waitlist handler unchanged |
| #5 | docs linode-runbook | none | |
| #6 | docs adhd-portal-spec | none | |
| #7 | stripe-webhook-signature | none | |
| #9 | fix/customer-links-real-pages | none | not in Watcher's QC list. Brann to decide whether/when |

Rule of thumb for #4: merge it **last**. It conflicts with #11, #13, #14 and the Landing
chain, so resolving it once against the finished stack is the least work.

## 3. Hand resolutions (five here = Watcher's four in the final QC)

Watcher's final full-stack QC ("Final full-stack QC (waitlist go/no-go)", verdict GO) resolved
four conflicting merges by hand. They are the same fixes as the five resolutions below; this guide
splits Watcher's #13 item into two (`.env.example` and the Landing imports):

| Watcher final QC | This guide | Same result? |
|---|---|---|
| 1. #18 vs #10, `backend/server.py` health: ping, then `waitlist_index_healthy()`, then "ok"; on an exception "degraded" with "service unavailable" | Resolution 1 | Yes, same code |
| 2. #13, `backend/.env.example` and the `Landing.jsx` import: keep both (#11–#15's FORWARDED_ALLOW_IPS, Turnstile, email and WEB_CONCURRENCY block plus #13's WAITLIST_ONLY and APP_ENV; both the TurnstileWidget and isWaitlistOnly imports) | Resolutions 2 and 3 | Yes. With #27 merged first, its `WAITLIST_DB_TIMEOUT_MS` line is part of the kept block too |
| 3. #16, `Landing.jsx`: React import `useCallback, useEffect, useRef, useState`; #11's honeypot and `<TurnstileWidget>`, then #16's button | Resolution 4 | Yes |
| 4. #19 (1572e8c), `Landing.jsx` form: #19's labelled fields and role=alert error, honeypot and TurnstileWidget before the error line, old unlabelled inputs dropped | Resolution 5 | Yes |

Watcher's check after all four: exactly **one honeypot, one Turnstile widget and one submit
button**, and the health logic as in resolution 1. No other merge in the stack (including
#25–#36) needs hand resolution.

### Resolution 1: #18 vs #10, `backend/server.py` (health endpoint)

> **NEVER "keep both sides" here.** Keeping both is valid Python, so nothing fails, but the
> first `return {"status": "ok"}` makes the WL-040 index check unreachable: health would say
> "ok" with no unique email index (Watcher's final QC). Replace the whole conflict block with
> exactly this:

```python
        await db.command("ping")
        from routers.marketing import waitlist_index_healthy  # EMP-WL-040: no unique email index
        if not waitlist_index_healthy():
            return {"status": "degraded"}
        return {"status": "ok"}
    except Exception:
        logging.getLogger("aio").exception("health: database ping failed")
        return {"status": "degraded", "detail": "service unavailable"}
```

Check: the block contains exactly one `return {"status": "ok"}`, it comes **after**
`waitlist_index_healthy()`, and `GET /api/health` returns no exception text.

### Resolution 2: #13, `backend/.env.example`

Keep both sides. Every variable name from both branches stays (names only, no values): #11–#15's
`FORWARDED_ALLOW_IPS`, Turnstile, email and `WEB_CONCURRENCY` block, #13's `WAITLIST_ONLY` and
`APP_ENV`, and (if #27 is already merged) `WAITLIST_DB_TIMEOUT_MS`.

### Resolution 3: #13, `frontend/src/pages/Landing.jsx` imports

Keep both sides. Every import from both branches stays (`TurnstileWidget` from #11 and
`isWaitlistOnly` from #13). Remove exact duplicate lines only.

### Resolution 4: #16, `frontend/src/pages/Landing.jsx`

- React import becomes: `import { useCallback, useEffect, useRef, useState } from "react";`
- In the form: keep #11's honeypot block and `<TurnstileWidget onToken={onTurnstileToken} />`,
  **then** #16's submit button.

### Resolution 5: #19, `frontend/src/pages/Landing.jsx`

- Take #19's labelled fields and its `wlError` alert.
- Put #11's honeypot block and `<TurnstileWidget onToken={onTurnstileToken} />` back in,
  right **before** the `{wlError && …}` line.
- Drop HEAD's old unlabelled inputs (exactly one email, one industry, one submit should remain).

## 4. Honeypot / Turnstile keep rule (EMP-WL-070), applies to #4, #16, #19 (and #2)

PR #11 added a hidden honeypot field (`wl-website`) and `<TurnstileWidget onToken={onTurnstileToken} />`
to the waitlist form, plus the `useCallback` import they need. When a later PR conflicts in
`Landing.jsx`, taking "their side" **silently deletes the bot protection**. The build still
passes and the form still works, so nothing visibly breaks.

When resolving #4, #16, #19 (or #2):

1. Keep both import sets and both hook blocks (`useCallback` must stay).
2. Keep the incoming PR's inputs (labels, translations), **plus** #11's honeypot block and
   `<TurnstileWidget onToken={onTurnstileToken} />`.
3. Check after resolving, from the repo root:

   ```bash
   f=frontend/src/pages/Landing.jsx
   rg -c 'wl-website" name' $f     # expect 1
   rg -c '<TurnstileWidget' $f      # expect 1
   rg -c 'data-testid="waitlist-submit"' $f   # expect 1
   cd frontend && CI=true npx craco test --watchAll=false src/pages/Landing.waitlist.test.js
   ```

## 5. General rules

- No force-push or rebase on these branches. Merge commits only.
- After each conflicted merge, run the backend unit tests (`cd backend && pytest -q tests/test_fix_*.py tests/test_waitlist_*.py tests/test_admin_reset_real_mongo.py tests/test_estimator_waitlist_fields.py`) and frontend tests
  (`cd frontend && CI=true npx craco test --watchAll=false`) before the next merge.
- Merging is not deploying. Deploys stay a separate step that Brann approves.
