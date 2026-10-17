"""Contact email / site URL are env-driven, no real domain hard-coded (removes aioffice.io)."""
import asyncio
import pathlib

import site_contact
from routers import marketing


def test_defaults_are_placeholders(monkeypatch):
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("SITE_URL", raising=False)
    assert site_contact.contact_email() == "contact@example.com"
    monkeypatch.delenv("NICHE", raising=False)
    assert site_contact.site_url() == "https://mindateasesolutions.com"


def test_env_override(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", " ops@test.invalid ")
    monkeypatch.setenv("SITE_URL", "https://site.test/")
    assert site_contact.contact_email() == "ops@test.invalid"
    assert site_contact.site_url() == "https://site.test"


def _confirmation_html(monkeypatch):
    sent = {}

    async def fake_send(*, to, subject, html, **kw):
        sent["html"] = html

    monkeypatch.setenv("WAITLIST_CONFIRMATION_EMAIL", "true")
    import services.email as em
    monkeypatch.setattr(em, "send_email", fake_send)
    asyncio.run(marketing._send_waitlist_confirmation("a@example.com"))
    return sent["html"]


def test_confirmation_email_links_site_url_only_when_set(monkeypatch):
    monkeypatch.delenv("SITE_URL", raising=False)
    monkeypatch.delenv("NICHE", raising=False)
    html = _confirmation_html(monkeypatch)
    assert "href='https://mindateasesolutions.com/'" in html
    monkeypatch.setenv("SITE_URL", "https://site.test")
    html = _confirmation_html(monkeypatch)
    assert "href='https://site.test/'" in html
    assert "aioffice" not in html.lower()


def test_no_legacy_domain_in_backend_source():
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = [str(p) for p in root.rglob("*.py")
            if "tests" not in p.parts and "aioffice.io" in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == []
