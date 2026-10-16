# Pilot end-to-end test matrix — Cloudflare-2
Date: 2026-10-08. Planning and route inventory only; NO tests executed here.

| Flow | Minimum sandbox checks | Exit criteria |
| --- | --- | --- |
| Signup | Create test tenant, verify unique workspace | User and tenant isolated |
| Login/logout | Correct and incorrect credentials, session expiry | Expected authentication behavior |
| Customers | Create, update, list, and cross-tenant denial | No cross-tenant data exposure |
| Invoice | Create draft, approve, send, read customer view | Correct totals and tenant ownership |
| Reminder | Schedule, duplicate trigger, paid-state suppression | No duplicate or post-payment messages |
| Receipt | Simulate completed sandbox payment | Receipt matches invoice and ledger |
| Payment | Stripe test-mode Connect onboarding and webhook | Verified signature, idempotent paid state |
| AI assistant | Text demo, refusal/fallback, workspace grounding | Useful response without cross-tenant leakage |

## Prerequisites
- Reachable staging frontend and backend, isolated test database and accounts.
- Test-mode payment integration enabled; no live charges.
- Controlled email sink and webhook test endpoint.
- Record actual request results, evidence, and cleanup steps.

Known blocker: no staging deployment or connected Stripe test account was verified during this review. Existing repository test reports are historical, not fresh evidence.
