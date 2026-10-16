"""MongoDB connection singleton."""
import os
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None
_db = None
_waitlist_client: AsyncIOMotorClient | None = None

# EMP-WL-061: the public waitlist signup must fail fast when MongoDB is down. With the driver
# defaults, server selection waits 30 s before the 503. Estimate: 5 s is enough for a cold TLS
# connection to a healthy cluster (usually well under 1 s) while keeping the error quick.
# Override with WAITLIST_DB_TIMEOUT_MS.
WAITLIST_DB_TIMEOUT_MS_DEFAULT = 5000


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _client


def get_db():
    global _db
    if _db is None:
        _db = get_client()[os.environ["DB_NAME"]]
    return _db


# Marks the app's own get_db. Code that swaps in another DB (tests, the waitlist-only app wiring)
# replaces the function, so the marker is gone and that DB is used as is (EMP-WL-061).
get_db.app_default = True


def waitlist_db_timeout_ms() -> int:
    try:
        value = int(os.environ.get("WAITLIST_DB_TIMEOUT_MS") or WAITLIST_DB_TIMEOUT_MS_DEFAULT)
    except ValueError:
        value = WAITLIST_DB_TIMEOUT_MS_DEFAULT
    return min(max(value, 500), 30000)


def get_waitlist_db():
    """Same database as get_db(), but a separate client with a short deadline for EVERY operation:
    server selection, connect and each command (timeoutMS, the driver's maxTimeMS successor). Used
    only by the public waitlist signup, so a down DB returns 503 in seconds. Everything else keeps
    get_db() and the driver defaults."""
    global _waitlist_client
    if _waitlist_client is None:
        ms = waitlist_db_timeout_ms()
        _waitlist_client = AsyncIOMotorClient(
            os.environ["MONGO_URL"], serverSelectionTimeoutMS=ms, connectTimeoutMS=ms, timeoutMS=ms)
    return _waitlist_client[os.environ["DB_NAME"]]


async def close_db():
    global _client, _waitlist_client
    if _client is not None:
        _client.close()
        _client = None
    if _waitlist_client is not None:
        _waitlist_client.close()
        _waitlist_client = None
