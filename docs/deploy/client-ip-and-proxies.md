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
| `docker compose` (this repo), Nginx or cloudflared on the host | compose gateway `172.30.0.1` (fixed in `docker-compose.yml`) | `172.30.0.1` |
| Cloudflare proxy straight to uvicorn, no Nginx | Cloudflare edge IPs (CIDR ranges) | **Not supported on uvicorn 0.25.** Put Nginx in front, use a Tunnel, or upgrade uvicorn to 0.31+ (CF-003) and list the ranges. |

Never use `*`.

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
