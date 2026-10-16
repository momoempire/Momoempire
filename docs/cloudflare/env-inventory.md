# Cloudflare-2 configuration inventory

Database: MONGO_URL and DB_NAME. Auth: JWT_SECRET and private admin credentials. Origins: CORS_ORIGINS and FRONTEND_URL. AI: AI_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY. Email: RESEND_API_KEY (+ RESEND_FROM_EMAIL). Payments: STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET and STRIPE_CONNECT_WEBHOOK_SECRET. Jobs: WEBHOOK_CRON_SECRET. Optional integrations: TWILIO_* and GOOGLE_*.

Store actual values only in approved host secret settings. No production keys or external service connections were provisioned during this audit.
