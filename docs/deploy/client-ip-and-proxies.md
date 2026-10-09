# Client IP behind proxies (rate limits, waitlist, demo)

The backend's per-IP limits (public waitlist, AI demo, login lockout) use `request.client.host`.
Uvicorn sets that from `X-Forwarded-For` **only** when the direct peer is listed in
`FORWARDED_ALLOW_IPS`. Anyone else's `X-Forwarded-For` is ignored, so it cannot be spoofed.

- `backend/Dockerfile` runs `uvicorn --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"`. It used to be `'*'`, which trusted any client's header.
- The pinned `uvicorn==0.25.0` accepts **exact IPs only**. CIDR ranges in `FORWARDED_ALLOW_IPS` arrived in uvicorn 0.31.0 ([release notes](https://github.com/encode/uvicorn/blob/master/docs/release-notes.md#0310-september-27-2024)). So Cloudflare's ranges are configured in Nginx, not uvicorn.
- `docker-compose.yml` publishes `127.0.0.1:8001:8001`. Port 8001 is not reachable from the internet; traffic must come through Nginx or a Cloudflare Tunnel on the same host.

## What to set

| Setup | Backend peer seen by uvicorn | `FORWARDED_ALLOW_IPS` |
|---|---|---|
| Uvicorn directly on the host, Nginx or cloudflared on the same host | `127.0.0.1` | empty (defaults to `127.0.0.1`) |
| `docker compose` (this repo), Nginx or cloudflared on the host | compose gateway, `172.30.0.1` by default | set automatically by `docker-compose.yml` from `AIO_COMPOSE_GATEWAY` (default `172.30.0.1`) |
| Cloudflare proxy straight to uvicorn, no Nginx | Cloudflare edge IPs (CIDR ranges) | **Not supported on uvicorn 0.25.** Put Nginx in front, use a Tunnel, or upgrade uvicorn to 0.31+ (CF-003) and list the ranges. |

Never use `*`.

### Compose subnet (EMP-WL-025)
`FORWARDED_ALLOW_IPS` **must equal the compose network's gateway**. If it doesn't, uvicorn ignores
`X-Forwarded-For` from your proxy, and every visitor shares the proxy's rate-limit bucket.
- The default network is `172.30.0.0/24`, with gateway `172.30.0.1`. That can clash with an existing
  Docker network or a VPN route on the host. Compose then fails with "Pool overlaps with other one
  on this address space".
- To change it, set both values in a `.env` file next to `docker-compose.yml`:
  ```
  AIO_COMPOSE_SUBNET=10.213.47.0/24
  AIO_COMPOSE_GATEWAY=10.213.47.1
  ```
  Pick any private /24 that `ip route` and `docker network inspect` don't already show.
- `docker-compose.yml` sets the backend's `FORWARDED_ALLOW_IPS` from `AIO_COMPOSE_GATEWAY`, so the
  two can't drift. The compose value overrides any `FORWARDED_ALLOW_IPS` in `backend/.env`.
- To check: `docker compose config | grep -A3 ipam`, then send one request through the proxy and
  confirm the stored waitlist `ip` is the visitor's.

## One worker (EMP-WL-021)
The waitlist and demo rate limits (5/min and 30/h per IP for the waitlist, 20/min for the demo) are
kept **in process memory**. With N uvicorn workers, each worker has its own counters, so a client can
get up to N times the limit. Watcher measured 7 of 12 accepted with 2 workers, instead of 5. Counters
also reset on restart.
- The Dockerfile runs `--workers 1`, and both the image and compose set `WEB_CONCURRENCY=1`.
- The app logs an **error** at startup if `WEB_CONCURRENCY` > 1, for example on a non-Docker host
  running `uvicorn` with a higher value.
- Don't run compose `--scale backend=N` or multiple replicas behind one domain for the waitlist.
- One worker is enough for a waitlist. Each signup is a few ms of work (Watcher measured a median of
  about 3–4 ms; estimate). If the full app ever needs more workers, move the limiter to a shared
  store first (MongoDB TTL counter, Redis, or Cloudflare rate limiting rules).

## Production value: Cloudflare's IP ranges (in Nginx)
Cloudflare publishes its edge ranges here (official): **https://www.cloudflare.com/ips/**. Plain-text lists:
- https://www.cloudflare.com/ips-v4
- https://www.cloudflare.com/ips-v6

When Cloudflare proxies to Nginx, configure Nginx to take the client IP from Cloudflare only. This uses the [ngx_http_realip_module](https://nginx.org/en/docs/http/ngx_http_realip_module.html) and [Cloudflare's guide to restoring visitor IPs](https://developers.cloudflare.com/support/troubleshooting/restoring-visitor-ips/restoring-original-visitor-ips/):

```nginx
# One line per range from https://www.cloudflare.com/ips-v4 and /ips-v6 (re-check periodically).
set_real_ip_from 173.245.48.0/20;
# ...
real_ip_header CF-Connecting-IP;

location /api/ {
    proxy_pass http://127.0.0.1:8001;
    # Overwrite (do not append) so uvicorn sees exactly one, already-verified client IP.
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Host $host;
}
```

Nginx then sets `$remote_addr` to the visitor IP only for requests from Cloudflare ranges. Uvicorn trusts only Nginx (`127.0.0.1`, or `172.30.0.1` under compose).

**Cloudflare Tunnel:** `cloudflared` connects from the same host, so the peer is the loopback (or the compose gateway). Uvicorn takes the right-most `X-Forwarded-For` entry that is not a trusted proxy. Cloudflare appends the real visitor IP to any client-supplied value, so that entry should be the real visitor. **Not verified here:** before relying on it, confirm with one test request through the tunnel (send a fake `X-Forwarded-For` and check the IP stored on the waitlist row). Otherwise, put Nginx with `real_ip_header CF-Connecting-IP` in front.

The open PR #5 (Linode runbook, `docs/deploy/linode-runbook.md`) should be updated to match: loopback-only port, `FORWARDED_ALLOW_IPS`, and the Nginx real-IP block above. This PR does not edit it.
