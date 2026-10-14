"""Backend brand is one env-driven setting (provisional, not legally cleared)."""
import brand


def test_defaults(monkeypatch):
    monkeypatch.delenv("BRAND_NAME", raising=False)
    monkeypatch.delenv("BRAND_SLOGAN", raising=False)
    monkeypatch.delenv("NICHE", raising=False)
    assert brand.brand_name() == "Mind At Ease Solutions HVAC"
    assert brand.brand_site_url() == "https://hvac.mindateasesolutions.com"
    assert brand.brand_slogan() == "Your slogan here"


def test_env_override(monkeypatch):
    monkeypatch.setenv("BRAND_NAME", " Acme Test ")
    monkeypatch.setenv("BRAND_SLOGAN", "Slogan X")
    assert brand.brand_name() == "Acme Test"
    assert brand.brand_slogan() == "Slogan X"
    from services.email import _frame
    assert "Acme Test" in _frame("<p>x</p>")


def test_per_niche(monkeypatch):
    monkeypatch.delenv("BRAND_NAME", raising=False)
    monkeypatch.setenv("NICHE", "Plumbing")
    assert brand.brand_name() == "Mind At Ease Solutions Plumbing"
    assert brand.brand_site_url() == "https://plumbing.mindateasesolutions.com"
    assert brand.brand_for_niche("pest-control")["name"] == "Mind At Ease Solutions Pest Control"
    assert brand.brand_for_niche("bad niche!")["niche"] == "hvac"
