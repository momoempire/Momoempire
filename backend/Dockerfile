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
    CMD python -c 'import json,os,sys,urllib.request; d=json.load(urllib.request.urlopen("http://localhost:"+os.getenv("PORT","8001")+"/api/health",timeout=4)); sys.exit(0 if d.get("status")=="ok" and d.get("db")=="ok" else 1)'

# Single worker is fine for low usage; raise with --workers for busier hosts.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
