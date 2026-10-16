"""Email service — direct Resend API with the operator's own key. Follow playbook guardrails."""
import os

from brand import brand_name
import re
import ipaddress
import logging
import httpx
from html import escape as html_escape
from html.parser import HTMLParser
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Direct Resend API (operator-owned account + key).
EMAIL_BASE_URL = "https://api.resend.com"


def _cfg():
    return {
        "key": os.environ.get("RESEND_API_KEY"),
        "from_email": os.environ.get("RESEND_FROM_EMAIL"),
        "from_name": os.environ.get("EMAIL_FROM_NAME") or brand_name(),
        "reply_to": os.environ.get("EMAIL_REPLY_TO"),
    }


_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = (
    "reply with your password", "reply with the code", "send your password", "cvv",
    "send us your password", "enter your password below", "confirm your card number",
    "your full card number", "seed phrase", "recovery phrase", "verify your card",
    "social security number", "confirm your bank details",
)
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host); return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan(); scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} ≠ real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str,
                     from_name: str | None = None, reply_to: str | None = None) -> str | None:
    """Send a transactional email through the operator's own Resend account.
    Returns the Resend email id, or None when email is unconfigured
    (RESEND_API_KEY / RESEND_FROM_EMAIL unset) — soft fallback, callers
    treat None as queued/no-op."""
    cfg = _cfg()
    if not cfg["key"] or not cfg["from_email"]:
        logger.warning("RESEND_API_KEY/RESEND_FROM_EMAIL not set — skipping email send to %s (%s)", to, subject)
        print(f"[EMAIL skipped] to={to} subj={subject!r}")
        return None
    _assert_safe_email(subject, html)
    payload = {
        "from": f"{(from_name or cfg['from_name'])[:60]} <{cfg['from_email']}>",
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if reply_to or cfg["reply_to"]:
        payload["reply_to"] = reply_to or cfg["reply_to"]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{EMAIL_BASE_URL}/emails",
                headers={"Authorization": f"Bearer {cfg['key']}"},
                json=payload,
            )
        resp.raise_for_status()
        return (resp.json() or {}).get("id")
    except httpx.HTTPStatusError as e:
        logger.error("Email send failed: %s %s", e.response.status_code, e.response.text)
        return None
    except Exception as e:
        logger.error("Email send error: %s", e)
        return None


# ---------- Templates (server-side, never caller-supplied) ----------
def _frame(body_html: str, app_name: str | None = None) -> str:
    """Shared email wrapper. Inline styles only."""
    app_name = app_name or brand_name()
    return (
        '<table role="presentation" width="100%" style="background:#f6f7f9;padding:24px 0">'
        '<tr><td align="center">'
        '<table role="presentation" width="560" style="background:#ffffff;border-radius:14px;'
        'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Arial,sans-serif;'
        'color:#111;border:1px solid #eceff3">'
        f'<tr><td style="padding:26px 32px 8px 32px;font-size:12px;letter-spacing:.08em;'
        f'text-transform:uppercase;color:#8a8f98">{html_escape(app_name)}</td></tr>'
        f'<tr><td style="padding:4px 32px 28px 32px">{body_html}</td></tr>'
        '<tr><td style="padding:14px 32px 24px 32px;border-top:1px solid #eceff3;'
        'font-size:11px;color:#8a8f98">You are receiving this because your business workspace '
        f'is set up on {html_escape(app_name)}. We never ask for your password or card details by email.'
        '</td></tr></table></td></tr></table>'
    )


def invite_html(*, inviter: str, business: str, role: str, accept_url: str) -> str:
    body = (
        f'<p style="font-size:20px;margin:4px 0 10px">You are invited to join <strong>{html_escape(business)}</strong></p>'
        f'<p style="color:#555">{html_escape(inviter)} invited you to join the team as '
        f'<strong>{html_escape(role)}</strong>. Click the button below to accept — '
        'you can use Google or set a password.</p>'
        f'<p style="margin:22px 0"><a href="{html_escape(accept_url)}" '
        'style="display:inline-block;background:#0A0A0A;color:#ffffff;padding:12px 20px;'
        'border-radius:10px;text-decoration:none;font-weight:600">Accept invite</a></p>'
        '<p style="color:#8a8f98;font-size:12px">If you did not expect this, you can ignore it.</p>'
    )
    return _frame(body)


def review_request_html(*, business: str, review_url: str, customer_name: str = "") -> str:
    greet = f"Hi {html_escape(customer_name)}," if customer_name else "Hi there,"
    body = (
        f'<p style="font-size:20px;margin:4px 0 10px">A quick favor from {html_escape(business)}</p>'
        f'<p style="color:#555">{greet} thanks for giving us the chance to help. '
        'Would you take 30 seconds to leave a review? It genuinely helps us keep rates fair and the team paid.</p>'
        f'<p style="margin:22px 0"><a href="{html_escape(review_url)}" '
        'style="display:inline-block;background:#059669;color:#ffffff;padding:12px 20px;'
        'border-radius:10px;text-decoration:none;font-weight:600">Leave a 5★ review</a></p>'
    )
    return _frame(body)


def followup_html(*, business: str, message: str) -> str:
    body = (
        f'<p style="font-size:18px;margin:4px 0 10px">A note from {html_escape(business)}</p>'
        f'<p style="color:#333;line-height:1.55">{html_escape(message)}</p>'
    )
    return _frame(body)


def digest_html(*, business: str, period: str, stats: dict, highlights: list[str], tips: list[str]) -> str:
    kv = "".join(
        f'<tr><td style="padding:6px 0;color:#555">{html_escape(k)}</td>'
        f'<td style="padding:6px 0;text-align:right;font-weight:600">{html_escape(str(v))}</td></tr>'
        for k, v in stats.items()
    )
    hi = "".join(f'<li style="margin:4px 0">{html_escape(h)}</li>' for h in highlights[:6]) or "<li>Nothing noteworthy</li>"
    tp = "".join(f'<li style="margin:4px 0">{html_escape(t)}</li>' for t in tips[:5]) or ""
    body = (
        f'<p style="font-size:20px;margin:4px 0 10px">{html_escape(business)} · weekly digest</p>'
        f'<p style="color:#8a8f98;font-size:12px;margin:0 0 14px">{html_escape(period)}</p>'
        f'<table role="presentation" width="100%" style="border-collapse:collapse;margin-bottom:14px">{kv}</table>'
        '<p style="font-weight:600;margin:14px 0 6px">What stood out</p>'
        f'<ul style="margin:0 0 10px 20px;color:#333">{hi}</ul>'
        + (f'<p style="font-weight:600;margin:14px 0 6px">Try this week</p><ul style="margin:0 0 10px 20px;color:#333">{tp}</ul>' if tp else '')
    )
    return _frame(body)
