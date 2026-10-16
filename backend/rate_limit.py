"""Unified sliding-window rate limiter.

Interface first: `RateLimiter.check(key, limit, window_s)` returns
`(allowed: bool, retry_after_s: float)`.

- `RedisRateLimiter` when `REDIS_URL` is set — works across processes/workers.
- `InMemoryRateLimiter` otherwise — same semantics, per process (safe fallback).

Auth endpoints (login, 2FA challenge) use this module. The legacy per-module
limiters (e.g. marketing's SlidingLimiter) keep working; new code should use this.
"""
from __future__ import annotations

import os
import time
import logging

log = logging.getLogger("aio.rate_limit")


class RateLimiter:
    """Interface: sliding-window check."""

    def check(self, key: str, limit: int, window_s: float) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds). retry_after is 0 when allowed."""
        raise NotImplementedError

    def reset(self, key: str) -> None:
        raise NotImplementedError


class InMemoryRateLimiter(RateLimiter):
    """Per-process sliding window. Bounded memory under key-spraying."""

    MAX_KEYS = 50_000

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def check(self, key: str, limit: int, window_s: float) -> tuple[bool, float]:
        now = time.time()
        if len(self._hits) > self.MAX_KEYS:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < window_s}
        recent = [t for t in self._hits.get(key, []) if now - t < window_s]
        if len(recent) >= limit:
            self._hits[key] = recent
            oldest = min(recent) if recent else now
            retry_after = max(0.0, window_s - (now - oldest))
            return False, retry_after
        recent.append(now)
        self._hits[key] = recent
        return True, 0.0

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


class RedisRateLimiter(RateLimiter):
    """Cross-process sliding window on Redis sorted sets.

    Key layout: `rl:{key}` → ZSET of hit timestamps (ms, member = unique token).
    Uses a Lua script so check+trim is atomic.
    """

    _LUA = """
    local key = KEYS[1]
    local now_ms = tonumber(ARGV[1])
    local window_ms = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local token = ARGV[4]
    redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local retry_ms = window_ms
        if #oldest >= 2 then
            retry_ms = window_ms - (now_ms - tonumber(oldest[2]))
        end
        return {0, math.max(0, retry_ms) / 1000}
    end
    redis.call('ZADD', key, now_ms, token)
    redis.call('PEXPIRE', key, window_ms)
    return {1, 0}
    """

    def __init__(self, redis_url: str) -> None:
        from redis import asyncio as aioredis  # local import: redis is optional
        self._client = aioredis.from_url(redis_url, decode_responses=True)
        self._url = redis_url

    async def _run(self, key: str, limit: int, window_s: float):
        import secrets as _secrets

        now_ms = int(time.time() * 1000)
        token = f"{now_ms}:{_secrets.token_hex(8)}"
        return await self._client.eval(
            self._LUA, 1, f"rl:{key}", now_ms, int(window_s * 1000), limit, token
        )

    def check(self, key: str, limit: int, window_s: float) -> tuple[bool, float]:
        # Sync façade over the async client: used from async endpoints via
        # `await limiter.acheck(...)`. Kept sync-safe for non-async callers by
        # running the coroutine on a fresh loop only when no loop is running.
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            raise RuntimeError("RedisRateLimiter.check() called inside a running event loop; use acheck()")
        allowed, retry_after = asyncio.run(self._run(key, limit, window_s))
        return bool(allowed), float(retry_after)

    async def acheck(self, key: str, limit: int, window_s: float) -> tuple[bool, float]:
        allowed, retry_after = await self._run(key, limit, window_s)
        return bool(allowed), float(retry_after)

    def reset(self, key: str) -> None:
        import asyncio

        async def _del():
            await self._client.delete(f"rl:{key}")

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_del())
            return
        raise RuntimeError("RedisRateLimiter.reset() called inside a running event loop")


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Process-wide singleton: Redis when REDIS_URL is set, in-memory otherwise."""
    global _limiter
    if _limiter is None:
        redis_url = os.environ.get("REDIS_URL", "").strip()
        if redis_url:
            try:
                _limiter = RedisRateLimiter(redis_url)
                log.info("Rate limiter: Redis backend")
            except Exception as e:
                log.warning("REDIS_URL set but Redis init failed (%s); falling back to in-memory", e)
                _limiter = InMemoryRateLimiter()
        else:
            _limiter = InMemoryRateLimiter()
    return _limiter


def reset_rate_limiter_singleton() -> None:
    """Test hook: drop the cached singleton so env changes take effect."""
    global _limiter
    _limiter = None


async def check_rate_limit(key: str, limit: int, window_s: float) -> tuple[bool, float]:
    """Async-friendly check that works with either backend."""
    limiter = get_rate_limiter()
    if isinstance(limiter, RedisRateLimiter):
        return await limiter.acheck(key, limit, window_s)
    return limiter.check(key, limit, window_s)


def rate_limit_headers(retry_after: float) -> dict:
    return {"Retry-After": str(max(1, int(retry_after + 0.999)))}
