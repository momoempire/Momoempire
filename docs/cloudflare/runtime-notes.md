# Cloudflare-2 runtime decision

Use Cloudflare Pages for the compiled React frontend only. The application backend is Python FastAPI, runs via Uvicorn on port 8001, and uses Motor/MongoDB; it needs a separate Docker-compatible host. The frontend's REACT_APP_BACKEND_URL points to that HTTPS API origin.

Current repository has Pages redirects/headers, a backend Dockerfile, and a deployment health probe. Those files do not constitute a verified live deployment.

Pending: choose a backend host, confirm free-tier capacity, set private environment configuration, restrict credentialed CORS to approved origins, establish HTTPS and payment webhooks, configure scheduled jobs, and validate backups and end-to-end flows. No DNS, paid account, or production setting should change without owner approval.
