"""Automation rules engine — stores rules, evaluates triggers, runs actions."""
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone, timedelta
from db import get_db
from models import _now_iso
from models import AutomationRule, AutomationRuleIn
from security import require_tenant_user, require_tenant_owner_or_admin
from routers.usage import record_usage

router = APIRouter(prefix="/tenants/automation-rules", tags=["automation-rules"])


# Starter catalog of recipes a tenant can enable
STARTER_RULES = [
    {"name": "Missed call text-back", "trigger": "missed_call", "actions": [
        {"type": "send_sms", "params": {"template": "Sorry we missed you! Reply here and we'll get right back to you."}},
    ]},
    {"name": "Follow up new leads after 24h", "trigger": "new_lead", "conditions": {"age_hours": 24, "status": "new"}, "actions": [
        {"type": "send_sms", "params": {"template": "Hey {name}, just following up — still want help with that?"}},
        {"type": "mark_lead_followup", "params": {}},
    ]},
    {"name": "Appointment confirmation", "trigger": "appointment_booked", "actions": [
        {"type": "send_sms", "params": {"template": "Confirmed: {service} on {time}. Reply C to confirm, R to reschedule."}},
    ]},
    {"name": "Appointment reminder (24h before)", "trigger": "appointment_approaching", "conditions": {"hours_before": 24}, "actions": [
        {"type": "send_sms", "params": {"template": "Reminder: {service} tomorrow at {time}."}},
    ]},
    {"name": "Review request after job", "trigger": "job_completed", "actions": [
        {"type": "request_review", "params": {"channel": "sms"}},
    ]},
    {"name": "Chase unpaid invoice (7 days)", "trigger": "invoice_unpaid", "conditions": {"age_days": 7}, "actions": [
        {"type": "send_sms", "params": {"template": "Friendly reminder: invoice {title} for ${total} is awaiting payment."}},
    ]},
    {"name": "Maintenance reminder (6 months inactive)", "trigger": "customer_inactive", "conditions": {"days": 180}, "actions": [
        {"type": "send_sms", "params": {"template": "Hey {name} — it's been a while! Time for a tune-up?"}},
    ]},
    {"name": "Owner alert on high-value lead", "trigger": "high_value_lead", "conditions": {"min_score": 85}, "actions": [
        {"type": "notify_owner", "params": {"channel": "email"}},
    ]},
]


@router.get("/catalog")
async def catalog():
    return STARTER_RULES


@router.get("")
async def list_rules(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.automation_rules.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(100)


@router.post("")
async def create_rule(data: AutomationRuleIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    r = AutomationRule(tenant_id=user["tenant_id"], **data.model_dump())
    await db.automation_rules.insert_one(dict(r.model_dump()))
    return r.model_dump()


@router.post("/install-starters")
async def install_starters(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    created = 0
    for cfg in STARTER_RULES:
        if await db.automation_rules.find_one({"tenant_id": user["tenant_id"], "name": cfg["name"]}):
            continue
        r = AutomationRule(tenant_id=user["tenant_id"], name=cfg["name"], trigger=cfg["trigger"],
                           conditions=cfg.get("conditions", {}), actions=cfg["actions"], enabled=True)
        await db.automation_rules.insert_one(dict(r.model_dump()))
        created += 1
    return {"created": created}


@router.put("/{rid}")
async def update_rule(rid: str, data: AutomationRuleIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.automation_rules.update_one(
        {"id": rid, "tenant_id": user["tenant_id"]},
        {"$set": data.model_dump()},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return await db.automation_rules.find_one({"id": rid}, {"_id": 0})


@router.delete("/{rid}")
async def delete_rule(rid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.automation_rules.delete_one({"id": rid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}


async def _execute_actions(tenant_id: str, rule: dict, target: dict, kind: str) -> list[str]:
    """Execute each action and return log lines."""
    db = get_db()
    lines = []
    name = target.get("name") or target.get("customer_name") or ""
    phone = target.get("phone") or target.get("customer_phone") or ""
    for a in rule.get("actions", []):
        atype = a.get("type")
        params = a.get("params", {})
        if atype == "send_sms":
            tpl = params.get("template", "Hello {name}")
            body = (tpl
                    .replace("{name}", name)
                    .replace("{service}", target.get("service_name") or "")
                    .replace("{time}", target.get("start_at") or "")
                    .replace("{title}", target.get("title") or "")
                    .replace("{total}", str(target.get("total") or "")))
            print(f"[AUTO-SMS→{phone}] {body}")
            if phone:
                await record_usage(tenant_id, "sms", 1, {"rule_id": rule["id"], "target": target.get("id")})
            lines.append(f"SMS → {phone}: {body[:80]}")
        elif atype == "notify_owner":
            tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "contact_email": 1, "contact_phone": 1})
            dest = (tenant or {}).get("contact_email") or (tenant or {}).get("contact_phone")
            print(f"[AUTO-NOTIFY-OWNER→{dest}] {rule['name']} fired for {kind}")
            lines.append(f"Owner notified at {dest}")
        elif atype == "request_review":
            from models import ReviewRequest
            rr = ReviewRequest(
                tenant_id=tenant_id, customer_id=target.get("id"),
                customer_name=name, customer_phone=phone,
                customer_email=target.get("email") or "",
                channel=params.get("channel", "sms"),
                message=f"Thanks for choosing us! Would you leave a quick review?",
            )
            await db.review_requests.insert_one(dict(rr.model_dump()))
            lines.append(f"Review request created for {name}")
        elif atype == "mark_lead_followup":
            await db.leads.update_one({"id": target.get("id"), "tenant_id": tenant_id},
                                      {"$set": {"status": "contacted"}})
            lines.append(f"Lead {name} marked contacted")
        elif atype == "create_followup":
            lines.append(f"Follow-up task created for {name}")
    return lines


@router.post("/{rid}/run")
async def run_rule(rid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Execute a rule against all matching current records."""
    db = get_db()
    rule = await db.automation_rules.find_one({"id": rid, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not rule:
        raise HTTPException(404, "Rule not found")
    if not rule.get("enabled"):
        return {"status": "skipped", "reason": "disabled"}

    trigger = rule["trigger"]
    conditions = rule.get("conditions", {})
    now = datetime.now(timezone.utc)
    executed = 0
    log = []

    if trigger == "new_lead":
        age_hours = int(conditions.get("age_hours", 0))
        cutoff = (now - timedelta(hours=age_hours)).isoformat()
        status = conditions.get("status", "new")
        async for lead in db.leads.find({"tenant_id": user["tenant_id"], "status": status, "created_at": {"$lte": cutoff}}, {"_id": 0}):
            log += await _execute_actions(user["tenant_id"], rule, lead, "lead")
            executed += 1
            if executed >= 50: break
    elif trigger == "missed_call":
        async for conv in db.conversations.find({"tenant_id": user["tenant_id"], "status": "missed"}, {"_id": 0}).limit(50):
            log += await _execute_actions(user["tenant_id"], rule, conv, "conversation")
            executed += 1
    elif trigger == "job_completed":
        async for a in db.appointments.find({"tenant_id": user["tenant_id"], "status": "completed"}, {"_id": 0}).limit(50):
            log += await _execute_actions(user["tenant_id"], rule, a, "appointment")
            executed += 1
    elif trigger == "appointment_approaching":
        hours_before = int(conditions.get("hours_before", 24))
        window_start = now
        window_end = now + timedelta(hours=hours_before)
        async for a in db.appointments.find({
            "tenant_id": user["tenant_id"],
            "status": {"$in": ["scheduled", "confirmed"]},
            "start_at": {"$gte": window_start.isoformat(), "$lte": window_end.isoformat()},
        }, {"_id": 0}).limit(50):
            log += await _execute_actions(user["tenant_id"], rule, a, "appointment")
            executed += 1
    elif trigger == "invoice_unpaid":
        age_days = int(conditions.get("age_days", 7))
        cutoff = (now - timedelta(days=age_days)).isoformat()
        async for inv in db.invoices.find({"tenant_id": user["tenant_id"], "status": {"$in": ["sent", "overdue"]}, "created_at": {"$lte": cutoff}}, {"_id": 0}).limit(50):
            log += await _execute_actions(user["tenant_id"], rule, inv, "invoice")
            executed += 1
    elif trigger == "customer_inactive":
        days = int(conditions.get("days", 180))
        cutoff = (now - timedelta(days=days)).isoformat()
        async for cust in db.customers.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).limit(100):
            recent = await db.appointments.find_one({"tenant_id": user["tenant_id"], "customer_phone": cust.get("phone", "__none__"), "created_at": {"$gte": cutoff}})
            if not recent:
                log += await _execute_actions(user["tenant_id"], rule, cust, "customer")
                executed += 1
    elif trigger == "appointment_booked":
        async for a in db.appointments.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).limit(50):
            log += await _execute_actions(user["tenant_id"], rule, a, "appointment")
            executed += 1

    await db.automation_rules.update_one({"id": rid}, {"$set": {"last_run_at": _now_iso()}, "$inc": {"run_count": 1}})
    return {"executed": executed, "log": log[:100]}
