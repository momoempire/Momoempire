"""Public embed widget: a tiny lead-capture + chat snippet businesses paste on their site.

GET /api/public/widget/{slug}.js  → returns a JS snippet that renders a floating
widget on any site. The widget POSTs leads to /api/public/widget/{slug}/lead.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from typing import Optional
from db import get_db
from models import _uuid, _now_iso, Lead
from routers.usage import record_usage

router = APIRouter(prefix="/public/widget", tags=["widget-public"])


@router.get("/{slug}.js")
async def widget_script(slug: str):
    db = get_db()
    tenant = await db.tenants.find_one({"slug": slug}, {"_id": 0, "id": 1, "name": 1, "branding": 1, "slug": 1})
    if not tenant:
        return Response("console.warn('[widget] unknown workspace');", media_type="application/javascript")
    primary = (tenant.get("branding") or {}).get("primary_color") or "#0A0A0A"
    name = tenant.get("name", "us")
    api_base = ""  # relative — the widget calls same-origin via the host's reverse proxy, or set absolute below
    js = f"""
(function() {{
  if (window.__aioWidgetLoaded) return;
  window.__aioWidgetLoaded = true;
  var SLUG = {slug!r};
  var NAME = {name!r};
  var PRIMARY = {primary!r};
  var API = {api_base!r} || (window.location.protocol + '//' + window.location.host);
  var btn = document.createElement('button');
  btn.setAttribute('data-aio-widget', 'launcher');
  btn.innerHTML = '💬 Talk to ' + NAME;
  btn.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:999999;background:'+PRIMARY+';color:#fff;border:0;border-radius:999px;padding:12px 18px;font:14px -apple-system,Segoe UI,Arial;cursor:pointer;box-shadow:0 10px 30px rgba(0,0,0,.18)';
  document.body.appendChild(btn);
  var panel = document.createElement('div');
  panel.style.cssText = 'position:fixed;bottom:76px;right:20px;z-index:999999;width:320px;background:#fff;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.18);display:none;overflow:hidden;font:14px -apple-system,Segoe UI,Arial;color:#111';
  panel.innerHTML = '<div style="padding:14px 16px;background:'+PRIMARY+';color:#fff;font-weight:600">Message '+NAME+'</div>'
    +'<form id="aioWF" style="padding:14px 16px;display:grid;gap:8px">'
    +'<input name="name" placeholder="Your name" required style="padding:9px;border:1px solid #ddd;border-radius:8px"/>'
    +'<input name="phone" placeholder="Phone" required style="padding:9px;border:1px solid #ddd;border-radius:8px"/>'
    +'<input name="email" type="email" placeholder="Email (optional)" style="padding:9px;border:1px solid #ddd;border-radius:8px"/>'
    +'<textarea name="message" placeholder="How can we help?" rows="3" style="padding:9px;border:1px solid #ddd;border-radius:8px;resize:vertical"></textarea>'
    +'<button type="submit" style="background:'+PRIMARY+';color:#fff;border:0;border-radius:10px;padding:10px;font-weight:600;cursor:pointer">Send</button>'
    +'<div id="aioMsg" style="font-size:12px;color:#555"></div>'
    +'</form>';
  document.body.appendChild(panel);
  btn.onclick = function() {{ panel.style.display = panel.style.display === 'none' ? 'block' : 'none'; }};
  document.getElementById('aioWF').onsubmit = function(e) {{
    e.preventDefault();
    var f = e.target, data = {{}};
    for (var el of f.elements) if (el.name) data[el.name] = el.value;
    var ref = new URLSearchParams(window.location.search).get('ref');
    if (ref) data.referral_code = ref;
    fetch(API + '/api/public/widget/' + SLUG + '/lead', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(data)
    }}).then(function(r) {{ return r.json(); }}).then(function(j) {{
      document.getElementById('aioMsg').textContent = j.ok ? 'Thanks! We\\'ll reach out shortly.' : (j.detail || 'Something went wrong.');
      if (j.ok) f.reset();
    }}).catch(function() {{ document.getElementById('aioMsg').textContent = 'Network error — try again.'; }});
  }};
}})();
""".strip()
    return Response(js, media_type="application/javascript")


class WidgetLeadIn(BaseModel):
    name: str
    phone: str
    email: Optional[EmailStr] = None
    message: str = ""
    referral_code: Optional[str] = None


@router.post("/{slug}/lead")
async def widget_lead(slug: str, data: WidgetLeadIn):
    db = get_db()
    tenant = await db.tenants.find_one({"slug": slug}, {"_id": 0, "id": 1})
    if not tenant:
        raise HTTPException(404, "unknown workspace")
    lead = Lead(
        tenant_id=tenant["id"],
        name=data.name.strip(),
        phone=data.phone.strip(),
        email=data.email,
        source="widget",
        notes=(data.message or "").strip(),
        status="new",
    )
    doc = lead.model_dump()
    if data.referral_code:
        doc["referral_code"] = data.referral_code.upper()
        await db.referrals.update_one({"code": data.referral_code.upper()}, {"$inc": {"conversions": 1}})
    await db.leads.insert_one(doc)
    await record_usage(tenant["id"], "ai_interactions", 1, {"source": "widget"})
    return {"ok": True}
