"""CORS origin validation for Cloudflare-2 deployments.

Env (names only): APP_ENV (production|staging = strict), CORS_ORIGINS (comma-separated
exact origins, e.g. "https://app.example.com,https://www.example.com").

Rules:
- An origin is scheme://host[:port] only: http/https, no userinfo, numeric port 1-65535,
  no path/query/fragment, no wildcard, no whitespace.
- Strict envs require https, and with no valid allowlist they FAIL CLOSED: no
  cross-origin request is allowed and an error is logged. The server still starts.
- Development with CORS_ORIGINS unset or "*": any origin may read responses, but
  credentials are never allowed with the wildcard.
- There is no permissive regex fallback.
"""
import logging
import os
import re
from urllib.parse import urlsplit

log = logging.getLogger("deploy-security")

STRICT_ENVS = ("production", "staging")
_HOST_RE = re.compile(r"^(?:\[[0-9a-fA-F:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)$")


def is_strict_env() -> bool:
    return os.getenv("APP_ENV", "development").strip().lower() in STRICT_ENVS


def validate_origin(origin: str, *, require_https: bool = False) -> str:
    """Return the normalized origin or raise ValueError."""
    if not isinstance(origin, str):
        raise ValueError("Invalid CORS origin")
    raw = origin.strip()
    if raw.endswith("/") and raw.count("/") == 3:  # tolerate a single trailing slash
        raw = raw[:-1]
    if not raw or "*" in raw or any(c.isspace() for c in raw) or "\\" in raw:
        raise ValueError("Invalid CORS origin")
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("Invalid CORS origin: scheme")
    netloc = parsed.netloc
    if not netloc or parsed.path or parsed.query or parsed.fragment or "?" in raw or "#" in raw:
        raise ValueError("Invalid CORS origin: must be scheme://host[:port] only")
    if "@" in netloc:
        raise ValueError("Invalid CORS origin: userinfo not allowed")
    host, port = netloc, None
    if netloc.startswith("["):
        end = netloc.find("]")
        if end == -1:
            raise ValueError("Invalid CORS origin: host")
        host, rest = netloc[: end + 1], netloc[end + 1:]
        if rest:
            if not rest.startswith(":"):
                raise ValueError("Invalid CORS origin: host")
            port = rest[1:]
    elif ":" in netloc:
        host, port = netloc.rsplit(":", 1)
    if port is not None:
        if not port.isdigit() or not port.isascii() or not 1 <= int(port) <= 65535:
            raise ValueError("Invalid CORS origin: port")
    if not _HOST_RE.match(host) or ".." in host:
        raise ValueError("Invalid CORS origin: host")
    if require_https and scheme != "https":
        raise ValueError("HTTPS required for staging and production")
    normalized = f"{scheme}://{host.lower()}"
    if port is not None:
        normalized += f":{int(port)}"
    return normalized


def cors_options() -> dict:
    """Keyword args for Starlette's CORSMiddleware. Never uses allow_origin_regex."""
    strict = is_strict_env()
    configured = os.getenv("CORS_ORIGINS", "").strip()
    base = {"allow_methods": ["*"], "allow_headers": ["*"]}

    if not configured or configured == "*":
        if strict:
            log.error("CORS_ORIGINS is not set (or is '*') with APP_ENV=%s; "
                      "failing closed: no cross-origin requests will be allowed.",
                      os.getenv("APP_ENV"))
            return {**base, "allow_origins": [], "allow_credentials": False}
        return {**base, "allow_origins": ["*"], "allow_credentials": False}

    origins: list[str] = []
    for item in configured.split(","):
        if not item.strip():
            continue
        try:
            o = validate_origin(item, require_https=strict)
        except ValueError as e:
            log.error("Ignoring CORS_ORIGINS entry %r: %s", item.strip(), e)
            continue
        if o not in origins:
            origins.append(o)

    if not origins:
        log.error("CORS_ORIGINS has no valid entries; failing closed: "
                  "no cross-origin requests will be allowed.")
        return {**base, "allow_origins": [], "allow_credentials": False}
    return {**base, "allow_origins": origins, "allow_credentials": True}
