# ---- Portable backend image for AI Office Platform ----
# Use on: Oracle Cloud Free Tier (ARM Ampere), Fly.io, Railway, Render,
# any VM, or Docker Compose. ~140 MB final image.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System deps (bcrypt, cryptography, etc. need build-essential + libffi).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python deps first for layer caching.
COPY requirements.txt /app/requirements.txt
# Install only public Python packages for a portable off-Emergent build.
# Private Emergent integrations are not required by llm_portable's OpenAI path.
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

# App code.
COPY . /app

# Non-root user for prod.
RUN useradd -m -u 10001 app && chown -R app:app /app
USER app

ENV PORT=8001
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/api/health || exit 1

# ONE worker, on purpose (EMP-WL-021): the waitlist and demo rate limits live in process memory,
# so N workers would allow N times the limit. Scale with a bigger box or a shared limiter first.
# The app logs an error at startup if WEB_CONCURRENCY > 1. See docs/deploy/client-ip-and-proxies.md.
ENV WEB_CONCURRENCY=1
# X-Forwarded-For is trusted ONLY from FORWARDED_ALLOW_IPS (comma-separated exact IPs; uvicorn 0.25
# has no CIDR support). Default: loopback only. Never use '*'. See docs/deploy/client-ip-and-proxies.md.
ENV FORWARDED_ALLOW_IPS=127.0.0.1
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
