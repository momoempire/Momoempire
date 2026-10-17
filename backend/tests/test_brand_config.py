"""Backend brand is one env-driven setting (provisional, not legally cleared)."""
import brand


def test_defaults(monkeypatch):
    monkeypatch.delenv("BRAND_NAME", raising=False)
    monkeypatch.delenv("BRAND_SLOGAN", raising=False)
    monkeypatch.delenv("NICHE", raising=False)
    assert brand.DEFAULT_NICHE == "generic"
    assert brand.brand_name() == "Mind At Ease Solutions"
    assert brand.brand_site_url() == "https://mindateasesolutions.com"
    assert brand.brand_slogan() == "Your slogan here"
    assert brand.brand_descriptor() == "AI Office Assistant"


def test_generic_face():
    g = brand.brand_for_niche("generic")
    assert g["niche"] == "generic"
    assert g["name"] == "Mind At Ease Solutions"
    assert g["descriptor"] == "AI Office Assistant"
    assert g["site_url"] == "https://mindateasesolutions.com"
    assert g["accent"] and g["glyph"]


def test_generic_default_has_no_trade_terms(monkeypatch):
    # Guard (books tradition): the generic default surface must never carry a
    # trade — a future pack/brand edit cannot silently re-leak one into the
    # parent face.
    monkeypatch.delenv("BRAND_NAME", raising=False)
    monkeypatch.delenv("NICHE", raising=False)
    surface = " ".join([
        brand.brand_name(), brand.brand_descriptor(), brand.brand_site_url(),
        brand.DEFAULT_BRAND_NAME, brand.DEFAULT_SITE_URL,
    ]).lower()
    for marker in ("hvac", "heating", "air conditioning", "furnace", "thermostat",
                   "snowflake", "plumbing", "plumber", "electrical", "electrician",
                   "roofing", "landscaping", "pest control", "garage door"):
        assert marker not in surface


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
    assert brand.brand_for_niche("hvac")["name"] == "Mind At Ease Solutions HVAC"
    assert brand.brand_for_niche("hvac")["site_url"] == "https://hvac.mindateasesolutions.com"
    assert brand.brand_for_niche("pest-control")["name"] == "Mind At Ease Solutions Pest Control"
    assert brand.brand_for_niche("bad niche!")["niche"] == "generic"
