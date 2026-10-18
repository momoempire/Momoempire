"""Track C (owner audit, 2026-10-17) — niche-pack depth guards.

Existence is not completeness: test_trade_packs.py pins the schema minimums via
validate_pack; this module pins the production depth the audit verified in all
33 packs (docs/track-c-niche-pack-audit.md), so a future pack edit that quietly
guts a pricebook, script, or template set fails loudly instead of shipping a
hollow industry.
"""
from trade_packs import list_packs, load_pack

PACK_IDS = [p["id"] for p in list_packs() if p.get("valid", True)]


def test_all_33_packs_present_and_valid():
    summaries = list_packs()
    assert len(summaries) == 33
    assert all(s.get("valid") for s in summaries), [s for s in summaries if not s.get("valid")]
    assert len(PACK_IDS) == 33


def test_pack_depth_floors():
    for pack_id in PACK_IDS:
        d = load_pack(pack_id)
        pb = d["pricebook"]
        assert len(pb) >= 10, f"{pack_id}: pricebook has {len(pb)} items (<10)"
        for item in pb:
            assert item.get("description"), f"{pack_id}: pricebook item lacks description: {item.get('name')}"
            assert item.get("cost") is not None, f"{pack_id}: pricebook item lacks cost: {item.get('name')}"
            assert item.get("duration_min") is not None, f"{pack_id}: pricebook item lacks duration_min: {item.get('name')}"
        assert len(d["appointment_types"]) >= 4, f"{pack_id}: appointment_types <4"
        script = d["receptionist_script"]
        assert len(script) >= 1500, f"{pack_id}: receptionist script {len(script)} chars (<1500)"
        assert "greeting" in script.lower(), f"{pack_id}: receptionist script has no greeting section"
        triage = d["triage"]
        assert len(triage) >= 4, f"{pack_id}: triage <4 questions"
        for q in triage:
            assert len(q.get("options", [])) >= 3, f"{pack_id}: triage {q.get('id')} has <3 options"
            assert q.get("route"), f"{pack_id}: triage {q.get('id')} has no route"
        equipment = d["equipment_types"]
        assert len(equipment) >= 3, f"{pack_id}: equipment_types <3"
        assert sum(1 for e in equipment if len(e.get("fields", [])) >= 3) >= 3, \
            f"{pack_id}: fewer than 3 equipment types track >=3 fields"
        automations = d["automation_templates"]
        assert len(automations) >= 3, f"{pack_id}: automation_templates <3"
        for a in automations:
            assert len(a.get("steps", [])) >= 3, f"{pack_id}: automation {a.get('name')} has <3 steps"
        channels = {r["channel"] for r in d["review_templates"]}
        assert channels == {"sms", "email"}, f"{pack_id}: review channels {channels} != sms+email"


def test_taglines_are_unique_across_packs():
    taglines = {}
    for pack_id in PACK_IDS:
        tag = load_pack(pack_id)["tagline"].strip().lower()
        assert tag not in taglines, f"tagline shared by {taglines.get(tag)} and {pack_id}"
        taglines[tag] = pack_id
