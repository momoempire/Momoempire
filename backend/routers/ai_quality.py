"""AI quality monitoring — flags issues in conversations."""
from fastapi import APIRouter, Depends, HTTPException
from db import get_db
from models import _now_iso
from models import QualityFlag, QualityIssueType
from security import require_tenant_user, require_platform_admin

router = APIRouter(prefix="/quality", tags=["ai-quality"])


FRUSTRATION_PHRASES = [
    "frustrated", "angry", "upset", "ridiculous", "this is terrible",
    "waste of time", "annoying", "awful", "worst", "cancel", "stop",
    "i already said", "you're not listening", "manager", "complaint",
]


async def scan_conversation(tenant_id: str, conversation_id: str):
    """Scan a conversation and insert quality flags for detected issues. Idempotent per issue type."""
    db = get_db()
    conv = await db.conversations.find_one({"id": conversation_id, "tenant_id": tenant_id})
    if not conv:
        return 0
    msgs = await db.conv_messages.find(
        {"conversation_id": conversation_id}, {"_id": 0},
    ).sort("created_at", 1).to_list(500)
    caller_msgs = [m for m in msgs if m["role"] == "caller"]
    ai_msgs = [m for m in msgs if m["role"] == "ai"]
    booked_action = any((m.get("action") or {}).get("type") == "book_appointment" for m in ai_msgs)
    escalated_action = any((m.get("action") or {}).get("type") == "escalate_to_human" for m in ai_msgs)
    all_caller_text = " ".join(m["content"].lower() for m in caller_msgs)

    issues = []
    # Customer frustration
    if any(p in all_caller_text for p in FRUSTRATION_PHRASES):
        issues.append((QualityIssueType.FRUSTRATION, "high", "Caller used frustration keywords."))
    # Very short call
    if conv.get("status") in {"completed", "voicemail"} and len(msgs) <= 2:
        issues.append((QualityIssueType.SHORT, "low", "Conversation ended after fewer than 2 exchanges."))
    # Failed booking: caller asked to book but no appointment created
    wants_book = any(k in all_caller_text for k in ["book", "appointment", "schedule"])
    if wants_book and not booked_action and conv.get("status") == "completed":
        issues.append((QualityIssueType.FAILED_BOOKING, "high", "Caller asked to book but no appointment was created."))
    # Failed transfer: caller asked for human but we didn't escalate
    wants_human = any(k in all_caller_text for k in ["human", "representative", "person", "owner", "manager"])
    if wants_human and not escalated_action:
        issues.append((QualityIssueType.FAILED_TRANSFER, "high", "Caller asked for a human but AI did not escalate."))
    # Unresolved: status active but ended_at none and no action taken
    if conv.get("status") == "active" and len(msgs) > 6:
        issues.append((QualityIssueType.UNRESOLVED, "medium", "Call is active with 6+ turns and no completion."))

    inserted = 0
    for issue_type, severity, message in issues:
        existing = await db.quality_flags.find_one({
            "tenant_id": tenant_id, "conversation_id": conversation_id, "issue_type": issue_type,
        })
        if existing:
            continue
        qf = QualityFlag(
            tenant_id=tenant_id, conversation_id=conversation_id,
            issue_type=issue_type, severity=severity, message=message,
        )
        await db.quality_flags.insert_one(dict(qf.model_dump()))
        inserted += 1
    return inserted


@router.post("/scan/{conversation_id}")
async def manual_scan(conversation_id: str, user: dict = Depends(require_tenant_user)):
    inserted = await scan_conversation(user["tenant_id"], conversation_id)
    return {"flags_added": inserted}


@router.post("/scan-all")
async def scan_all(user: dict = Depends(require_tenant_user), limit: int = 100):
    db = get_db()
    convs = await db.conversations.find({"tenant_id": user["tenant_id"]}, {"_id": 0, "id": 1}).sort("created_at", -1).to_list(limit)
    total = 0
    for c in convs:
        total += await scan_conversation(user["tenant_id"], c["id"])
    return {"flags_added": total, "scanned": len(convs)}


@router.get("/flags")
async def list_flags(user: dict = Depends(require_tenant_user), status: str | None = None):
    db = get_db()
    q = {"tenant_id": user["tenant_id"]}
    if status:
        q["status"] = status
    return await db.quality_flags.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)


@router.put("/flags/{flag_id}")
async def update_flag(flag_id: str, status: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    if status not in {"new", "acknowledged", "resolved"}:
        raise HTTPException(400, "Invalid status")
    res = await db.quality_flags.update_one(
        {"id": flag_id, "tenant_id": user["tenant_id"]},
        {"$set": {"status": status}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Flag not found")
    return {"status": "ok"}


@router.get("/summary")
async def tenant_summary(user: dict = Depends(require_tenant_user)):
    db = get_db()
    tid = user["tenant_id"]
    pipeline = [
        {"$match": {"tenant_id": tid}},
        {"$group": {"_id": "$issue_type", "count": {"$sum": 1}}},
    ]
    rows = await db.quality_flags.aggregate(pipeline).to_list(50)
    return {
        "by_issue": {r["_id"]: r["count"] for r in rows},
        "open": await db.quality_flags.count_documents({"tenant_id": tid, "status": "new"}),
        "total": await db.quality_flags.count_documents({"tenant_id": tid}),
    }


# ---------- Platform admin ----------
admin_router = APIRouter(prefix="/admin/quality", tags=["admin-quality"], dependencies=[Depends(require_platform_admin)])


@admin_router.get("/dashboard")
async def admin_dashboard():
    db = get_db()
    tenants = await db.tenants.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(500)
    out = []
    for t in tenants:
        total = await db.quality_flags.count_documents({"tenant_id": t["id"]})
        open_ = await db.quality_flags.count_documents({"tenant_id": t["id"], "status": "new"})
        high = await db.quality_flags.count_documents({"tenant_id": t["id"], "severity": "high"})
        if total:
            out.append({"tenant_id": t["id"], "tenant_name": t["name"], "total": total, "open": open_, "high_severity": high})
    out.sort(key=lambda x: -x["high_severity"])
    return out
