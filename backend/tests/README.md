# Backend tests

## Unit tests (no server needed)

    pytest -q $(ls tests/test_*.py | grep -v -E "test_phase|test_iteration10|test_call_to_payment")

## Integration suites (need a running test backend)

`backend_test.py`, `test_phase*.py`, `test_iteration10_cloudflare_prep.py` and `test_call_to_payment.py`
call a live backend. Only point them at a **throwaway test server and database**: they register users and write data.

**They need HTTPS.** Auth cookies are `Secure`, so over plain `http://` the session cookie is dropped and
authenticated calls return 401. Locally: uvicorn `--ssl-keyfile/--ssl-certfile` with a self-signed cert, and
`REQUESTS_CA_BUNDLE=<cert>` for the tests.

Environment (test-only values, never production credentials):

| Variable | Purpose |
|---|---|
| `REACT_APP_BACKEND_URL` | Test server, e.g. `https://localhost:8001` |
| `TEST_ADMIN_EMAIL` / `TEST_ADMIN_PASSWORD` | Platform admin on the test server (first-login password already changed). Admin tests skip when unset. |
| `TEST_OWNER_EMAIL` / `TEST_OWNER_PASSWORD` | Pre-registered owner (default `repeat-tester@example.com`); dependent tests skip if it cannot log in. |
| `MONGO_URL` / `DB_NAME` | Same DB as the test server (some tests read it directly). |
| `WEBHOOK_CRON_SECRET` | Must match the server; cron tests skip when unset. |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Stripe **test-mode** keys; payment/webhook tests skip when unset. |
| `TEST_EXPECTED_LLM_BACKEND` | Expected `llm_backend` from `/api/health/deployment` (default `none`). |

Notes:
- Use `@example.com` addresses; the API rejects reserved domains like `@example.test`.
- Some suites pass state between classes. Run them serially with `-n 0`; a test whose prerequisite did not pass is reported as skipped.
- Login and demo endpoints are rate limited; restart the test server between repeated runs to avoid 429s.
