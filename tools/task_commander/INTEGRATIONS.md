# Empire Task Commander — integration checkpoint (2026-10-14)

Status: **investigated, not connected**. Never describe these providers as operational until real credentials, permissions and end-to-end tests succeed.

## First integration candidates
- **Slack** — communication/notification transport, not source of truth. Build a Slack app with minimum OAuth scopes. Slack Events API supports incoming events by public HTTPS endpoint or Socket Mode, and Web API can post messages. Reference: https://docs.slack.dev/apis/events-api/ . Keep credentials in environment/secret store; verify Slack requests and guard against replay.
- **Grok (xAI)** — model worker via xAI API, requires a separately provisioned API key and billing/quota. Reference: https://docs.x.ai/overview . Do not assume an existing Grok bot or subscription grants API usage.
- **Claude** — model worker via Anthropic Messages API, requires a separate API key and appropriate access. Reference: https://platform.claude.com/docs/en/api/overview . Do not assume Claude app subscription provides API quota.
- **Codex, Manus, Muse, ChatGPT** — integration path unverified; investigate supported APIs/connectors individually. Do not automate through unauthorized personal sessions.

## Safe incremental rollout
1. Registry completed in `tools/task_commander/registry.py` (SQLite, standard-library only).
2. Run tests: `cd tools/task_commander && python -m unittest -v test_registry.py`.
3. Set up a dedicated Slack test channel and restricted bot permissions only after owner approves connection.
4. Implement Slack notifications for **claimed, review, done** events; do not give Slack exclusive ownership of tasks.
5. Implement one worker adapter using approved provider API credentials; log retries, quotas, costs and evidence.
6. Keep existing HVAC and Cloudflare production code untouched until regression tests and separate review pass.

**Architecture note:** SQLite is acceptable for one coordinator service on persistent storage. For multi-instance/multi-host claiming, move to a centrally hosted transactional database; do not share one SQLite file over network storage. The current CLI is a foundation, not an agent scheduler or tested live integration.
