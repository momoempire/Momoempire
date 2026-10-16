"""Video bytes storage stub.

TODAY: plain local disk under a configurable directory
(`VIDEO_ESTIMATES_STORAGE_DIR`, default `<backend>/data/video-estimates`).

LATER (marked TODO(LIVE)): swap `save_video_bytes` / `load_video_bytes`
for S3/R2/GCS with presigned URLs. The router only ever touches this
module's two functions plus `storage_key`, so the swap is contained here.

Local layout:  <dir>/<tenant_id>/<request_id>.<ext>
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

STORAGE_DIR = Path(os.getenv("VIDEO_ESTIMATES_STORAGE_DIR") or
                   Path(__file__).resolve().parent.parent / "data" / "video-estimates")

_CONTENT_TYPE_TO_EXT = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
}


def _safe_filename(name: str) -> str:
    base = Path(name or "walkthrough").name
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in base)[:80]


def _ext_for(content_type: str, filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext and len(ext) <= 8:
        return ext
    return _CONTENT_TYPE_TO_EXT.get((content_type or "").lower(), ".bin")


def save_video_bytes(*, tenant_id: str, request_id: str, filename: str,
                     content_type: str, data: bytes) -> str:
    """Persist video bytes. Returns an opaque `storage_key`.

    TODO(LIVE): upload to S3/R2 here and return the object key; keep the
    `storage_key` opaque so the router doesn't change.
    """
    if not data:
        raise ValueError("empty video bytes")
    # Tenant/request ids are our own uuids; still sanitize path components.
    safe_tenant = "".join(c if c.isalnum() or c in "_-" else "_"
                          for c in tenant_id)[:64]
    safe_req = "".join(c if c.isalnum() or c in "_-" else "_"
                       for c in request_id)[:64]
    target_dir = STORAGE_DIR / safe_tenant
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_req}{_ext_for(content_type, filename)}"
    target.write_bytes(data)
    return str(target.relative_to(STORAGE_DIR))


def load_video_bytes(storage_key: str) -> Optional[Tuple[bytes, str]]:
    """Read video bytes back. Returns (bytes, content_type) or None.

    TODO(LIVE): fetch from S3/R2 by object key; infer content_type from
    stored metadata or object metadata.
    """
    path = (STORAGE_DIR / storage_key).resolve()
    if STORAGE_DIR.resolve() not in path.parents:
        return None  # path traversal attempt → treat as missing
    if not path.is_file():
        return None
    import mimetypes
    content_type = mimetypes.guess_type(str(path))[0] or "video/mp4"
    return path.read_bytes(), content_type
