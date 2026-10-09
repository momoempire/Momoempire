"""Seed admin user, industry templates and country registry on startup."""
import os
from datetime import datetime, timezone
from security import hash_password, verify_password
from db import get_db


def _now():
    return datetime.now(timezone.utc).isoformat()


DEFAULT_INDUSTRIES = [
    {
        "slug": "hvac", "name": "HVAC", "icon": "wind",
        "description": "Heating, ventilation, air conditioning service businesses.",
        "ai_personality": "Reassuring, technically clear, respects urgency on no-heat/no-cool emergencies.",
        "services": [
            {"name": "AC Tune-Up", "duration_minutes": 60, "price": 129},
            {"name": "Furnace Repair", "duration_minutes": 90, "price": 189},
            {"name": "Emergency Service Call", "duration_minutes": 60, "price": 249},
        ],
        "appointment_types": ["Diagnostic", "Repair", "Install Quote", "Maintenance"],
        "intake_questions": ["Is the unit heating or cooling?", "What's the age of the system?", "Any strange noises or smells?"],
        "escalation_rules": ["Gas smell → human immediately", "No heat under 40°F → high priority"],
        "emergency_rules": ["Carbon monoxide alert → call 911 first", "Gas odor → do not operate"],
        "faqs": [
            {"question": "Do you offer financing?", "answer": "Yes, 0% APR for 12 months on qualifying installs."},
            {"question": "Service area?", "answer": "We serve a 30-mile radius from our shop."},
        ],
        "terminology": {"invoice": "work order", "customer": "homeowner"},
        "recommended_integrations": ["Google Calendar", "Twilio SMS", "Stripe", "QuickBooks"],
        "industry_automations": ["Seasonal tune-up reminders", "Post-service review request"],
    },
    {
        "slug": "plumbing", "name": "Plumbing", "icon": "droplets",
        "description": "Residential and commercial plumbing companies.",
        "ai_personality": "Calm, direct, triages leaks and emergencies first.",
        "services": [
            {"name": "Drain Cleaning", "duration_minutes": 60, "price": 149},
            {"name": "Water Heater Repair", "duration_minutes": 120, "price": 299},
            {"name": "Leak Detection", "duration_minutes": 90, "price": 189},
        ],
        "appointment_types": ["Service", "Install", "Inspection"],
        "intake_questions": ["Is water actively leaking?", "Is the water shut off?", "How many fixtures affected?"],
        "escalation_rules": ["Active flooding → dispatch same day"],
        "emergency_rules": ["Burst pipe → instruct customer to shut off main valve"],
        "faqs": [{"question": "Free estimates?", "answer": "Yes, for installs over $500."}],
        "recommended_integrations": ["Twilio SMS", "Stripe", "ServiceTitan"],
    },
    {
        "slug": "electrical", "name": "Electrical", "icon": "zap",
        "description": "Licensed electricians, residential + commercial.",
        "ai_personality": "Safety-first, precise about code and permits.",
        "services": [
            {"name": "Panel Upgrade Quote", "duration_minutes": 60, "price": 0},
            {"name": "Outlet / Switch Repair", "duration_minutes": 60, "price": 159},
            {"name": "EV Charger Install", "duration_minutes": 180, "price": 899},
        ],
        "appointment_types": ["Diagnostic", "Install", "Inspection", "Quote"],
        "emergency_rules": ["Sparking outlet → shut off breaker immediately"],
        "recommended_integrations": ["Google Calendar", "Stripe"],
    },
    {
        "slug": "roofing", "name": "Roofing", "icon": "home",
        "description": "Roof repair, replacement, inspections.",
        "ai_personality": "Confident, thorough on inspection details, weather-aware.",
        "services": [
            {"name": "Free Inspection", "duration_minutes": 60, "price": 0},
            {"name": "Leak Repair", "duration_minutes": 180, "price": 450},
        ],
        "appointment_types": ["Inspection", "Repair", "Full Replacement Quote"],
        "recommended_integrations": ["CompanyCam", "Stripe"],
    },
    {
        "slug": "landscaping", "name": "Landscaping", "icon": "trees",
        "description": "Landscape design, lawn care, hardscaping.",
        "ai_personality": "Friendly, seasonal, happy to upsell maintenance plans.",
        "services": [
            {"name": "Weekly Lawn Mow", "duration_minutes": 45, "price": 55},
            {"name": "Spring Cleanup", "duration_minutes": 180, "price": 299},
        ],
        "appointment_types": ["Estimate", "Service Visit"],
    },
    {
        "slug": "pest-control", "name": "Pest Control", "icon": "bug",
        "description": "Residential and commercial pest management.",
        "ai_personality": "Reassuring, discreet, educational on prevention.",
        "services": [
            {"name": "General Inspection", "duration_minutes": 45, "price": 0},
            {"name": "Quarterly Treatment", "duration_minutes": 60, "price": 149},
        ],
        "appointment_types": ["Inspection", "Treatment", "Follow-up"],
    },
    {
        "slug": "dental", "name": "Dental", "icon": "smile",
        "description": "General and cosmetic dentistry practices.",
        "ai_personality": "Warm, HIPAA-aware, never diagnoses — books and triages.",
        "services": [
            {"name": "New Patient Exam", "duration_minutes": 60, "price": 199},
            {"name": "Cleaning", "duration_minutes": 45, "price": 149},
            {"name": "Emergency Toothache", "duration_minutes": 30, "price": 149},
        ],
        "appointment_types": ["New Patient", "Cleaning", "Consult", "Emergency"],
        "intake_questions": ["Dental insurance provider?", "When was your last visit?"],
        "escalation_rules": ["Facial swelling → same-day appointment"],
        "recommended_integrations": ["Dentrix", "Stripe", "Twilio SMS"],
    },
    {
        "slug": "physical-therapy", "name": "Physical Therapy", "icon": "activity",
        "description": "PT clinics and sports rehab.",
        "ai_personality": "Encouraging, patient-focused, HIPAA-aware.",
        "services": [
            {"name": "Initial Evaluation", "duration_minutes": 60, "price": 189},
            {"name": "Follow-up Visit", "duration_minutes": 45, "price": 99},
        ],
        "appointment_types": ["Evaluation", "Treatment", "Re-evaluation"],
        "intake_questions": ["Do you have a referral?", "What's your primary complaint?"],
    },
    {
        "slug": "contractor", "name": "General Contractor", "icon": "hammer",
        "description": "General contractors and remodelers.",
        "ai_personality": "Professional, detail-oriented on scope and budget.",
        "services": [
            {"name": "Project Consultation", "duration_minutes": 60, "price": 0},
            {"name": "Site Visit", "duration_minutes": 90, "price": 0},
        ],
        "appointment_types": ["Consultation", "Site Visit", "Walk-through"],
    },
    {
        "slug": "independent", "name": "Independent Professional", "icon": "user",
        "description": "Consultants, coaches, freelancers.",
        "ai_personality": "Flexible, approachable, aligns to your personal brand.",
        "services": [
            {"name": "Discovery Call", "duration_minutes": 30, "price": 0},
            {"name": "Working Session", "duration_minutes": 60, "price": 250},
        ],
        "appointment_types": ["Discovery", "Working Session", "Follow-up"],
    },
]

# Comprehensive seed of Stripe-known business account countries.
# Statuses reflect Stripe's documented tiers at seed time; admins override from the console.
DEFAULT_COUNTRIES = [
    # Supported (standard business locations)
    ("US", "United States", "supported"), ("CA", "Canada", "supported"),
    ("GB", "United Kingdom", "supported"), ("IE", "Ireland", "supported"),
    ("AU", "Australia", "supported"), ("NZ", "New Zealand", "supported"),
    ("DE", "Germany", "supported"), ("FR", "France", "supported"),
    ("ES", "Spain", "supported"), ("IT", "Italy", "supported"),
    ("NL", "Netherlands", "supported"), ("BE", "Belgium", "supported"),
    ("AT", "Austria", "supported"), ("CH", "Switzerland", "supported"),
    ("SE", "Sweden", "supported"), ("NO", "Norway", "supported"),
    ("DK", "Denmark", "supported"), ("FI", "Finland", "supported"),
    ("PT", "Portugal", "supported"), ("PL", "Poland", "supported"),
    ("CZ", "Czech Republic", "supported"), ("SK", "Slovakia", "supported"),
    ("HU", "Hungary", "supported"), ("RO", "Romania", "supported"),
    ("BG", "Bulgaria", "supported"), ("GR", "Greece", "supported"),
    ("HR", "Croatia", "supported"), ("SI", "Slovenia", "supported"),
    ("EE", "Estonia", "supported"), ("LV", "Latvia", "supported"),
    ("LT", "Lithuania", "supported"), ("LU", "Luxembourg", "supported"),
    ("MT", "Malta", "supported"), ("CY", "Cyprus", "supported"),
    ("JP", "Japan", "supported"), ("SG", "Singapore", "supported"),
    ("HK", "Hong Kong", "supported"), ("MY", "Malaysia", "supported"),
    ("TH", "Thailand", "supported"), ("MX", "Mexico", "supported"),
    ("BR", "Brazil", "supported"), ("AE", "United Arab Emirates", "supported"),
    ("IN", "India", "supported"), ("LI", "Liechtenstein", "supported"),
    ("GI", "Gibraltar", "supported"),
    # Preview (invite / beta)
    ("ZA", "South Africa", "preview"), ("ID", "Indonesia", "preview"),
    ("PH", "Philippines", "preview"), ("VN", "Vietnam", "preview"),
    ("CL", "Chile", "preview"), ("CO", "Colombia", "preview"),
    ("PE", "Peru", "preview"), ("AR", "Argentina", "preview"),
    ("KR", "South Korea", "preview"),
    # Extended network (payments only, no business accounts)
    ("TR", "Turkey", "extended"), ("SA", "Saudi Arabia", "extended"),
    ("IL", "Israel", "extended"), ("EG", "Egypt", "extended"),
    ("NG", "Nigeria", "extended"), ("KE", "Kenya", "extended"),
    # Not supported
    ("CN", "China (mainland)", "unsupported"),
    ("RU", "Russia", "unsupported"),
    ("IR", "Iran", "unsupported"),
    ("KP", "North Korea", "unsupported"),
]

DEFAULT_FLAGS = [
    {"key": "feature.calls", "enabled": False, "description": "Live telephony / call recording"},
    {"key": "feature.messages", "enabled": False, "description": "Omnichannel inbox (SMS, email, chat)"},
    {"key": "feature.reviews", "enabled": False, "description": "Review aggregation + auto-responses"},
    {"key": "feature.automations", "enabled": False, "description": "Workflow automations engine"},
    {"key": "feature.website_builder", "enabled": False, "description": "Tenant website builder"},
    {"key": "feature.customer_portal", "enabled": False, "description": "Branded customer portal"},
    {"key": "feature.phone_numbers", "enabled": False, "description": "Managed phone numbers"},
    {"key": "feature.payments", "enabled": True, "description": "Stripe-powered payments"},
    {"key": "feature.advisor", "enabled": True, "description": "AI business advisor chat"},
]


async def seed_admin():
    """Create the first platform admin ONLY if none exists. Never touches an existing hash.

    - Email: ADMIN_EMAIL, no default. If unset, no platform admin is seeded and a warning is logged.
      Existing users' emails are never changed.
    - Password: ADMIN_PASSWORD is used only for the very first seed, and only if it
      passes the strength rule. Otherwise the admin gets a random unusable password
      (password_unusable=True) and must use the reset-token flow. Either way must_change_password=True.
    - Existing DBs: any platform admin whose hash still matches a known default password gets that
      hash REPLACED with a random unusable one, plus must_change_password=True and password_unusable=True,
      so knowing the old default can never yield admin access.
    """
    import logging
    import secrets
    from password_policy import password_problems, KNOWN_DEFAULT_PASSWORDS

    log = logging.getLogger("seed")
    db = get_db()

    existing_admins = await db.users.find({"role": "platform_admin"}).to_list(1000)
    if existing_admins:
        # Never reset an admin password EXCEPT to neutralize a known default.
        for adm in existing_admins:
            if adm.get("password_unusable"):
                continue
            h = adm.get("password_hash") or ""
            if h and any(verify_password(w, h) for w in KNOWN_DEFAULT_PASSWORDS):
                await db.users.update_one(
                    {"id": adm.get("id"), "role": "platform_admin"},
                    {"$set": {"password_hash": hash_password(secrets.token_urlsafe(48)),
                              "password_unusable": True,
                              "must_change_password": True,
                              "password_changed_at": _now()}},
                )
                log.warning("Platform admin id=%s still had a known default password; it was replaced with an "
                            "unusable one. Use forgot-password to set a new password.", adm.get("id"))
        return

    email = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    if not email:
        log.warning("ADMIN_EMAIL is not set; skipping platform admin seed. Set ADMIN_EMAIL "
                    "(and optionally ADMIN_PASSWORD) and restart to create the platform admin.")
        return

    # EMP-W-CF-027: ADMIN_EMAIL already belongs to a non-admin user. Inserting would hit the unique
    # email index and abort ALL seeding. Never promote or modify that user automatically (anyone who
    # registered with that address would become platform admin): warn, skip, carry on seeding.
    clash = await db.users.find_one({"email": email}, {"_id": 0, "id": 1, "role": 1})
    if clash:
        log.warning("ADMIN_EMAIL matches an existing %s user (id=%s); NOT seeding a platform admin and NOT "
                    "changing that user. Set ADMIN_EMAIL to an unused address and restart, or promote the "
                    "account by hand after verifying who owns it.", clash.get("role") or "non-admin", clash.get("id"))
        return

    env_pwd = os.environ.get("ADMIN_PASSWORD") or ""
    problems = password_problems(env_pwd) if env_pwd else ["not set"]
    unusable = False
    if env_pwd and not problems:
        pwd_hash = hash_password(env_pwd)
        log.info("Seeding platform admin %s with ADMIN_PASSWORD (must change at first login)", email)
    else:
        if env_pwd:
            log.error("ADMIN_PASSWORD rejected (%s); seeding admin with an unusable random password", "; ".join(problems))
        else:
            log.warning("ADMIN_PASSWORD not set; seeding admin with an unusable random password — use forgot-password to set one")
        pwd_hash = hash_password(secrets.token_urlsafe(48))
        unusable = True

    from models import _uuid
    from pymongo.errors import DuplicateKeyError
    try:
        await _insert_admin(db, _uuid, email, pwd_hash, unusable)
    except DuplicateKeyError:
        # Same clash, created between the check above and the insert (e.g. two instances starting).
        log.warning("ADMIN_EMAIL was registered concurrently; NOT seeding a platform admin (EMP-W-CF-027).")


async def _insert_admin(db, _uuid, email, pwd_hash, unusable):
    await db.users.insert_one({
        "id": _uuid(),
        "email": email,
        "password_hash": pwd_hash,
        "name": "Platform Admin",
        "role": "platform_admin",
        "tenant_id": None,
        "email_verified": True,
        "mfa_enabled": False,
        "must_change_password": True,
        "password_unusable": unusable,
        "created_at": _now(),
    })


async def seed_industries():
    db = get_db()
    from models import IndustryTemplate
    for cfg in DEFAULT_INDUSTRIES:
        if await db.industries.find_one({"slug": cfg["slug"]}):
            continue
        doc = IndustryTemplate(**cfg).model_dump()
        await db.industries.insert_one(doc)


async def seed_countries():
    db = get_db()
    from models import Country
    for code, name, status in DEFAULT_COUNTRIES:
        if await db.countries.find_one({"code": code}):
            continue
        doc = Country(code=code, name=name, status=status, enabled=(status in {"supported", "preview"})).model_dump()
        await db.countries.insert_one(doc)


async def seed_flags():
    db = get_db()
    from models import FeatureFlag
    for cfg in DEFAULT_FLAGS:
        if await db.feature_flags.find_one({"key": cfg["key"]}):
            continue
        doc = FeatureFlag(**cfg).model_dump()
        await db.feature_flags.insert_one(doc)


async def ensure_indexes():
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.users.create_index("id", unique=True)
    await db.users.create_index("tenant_id")
    await db.tenants.create_index("id", unique=True)
    await db.tenants.create_index("slug", unique=True)
    await db.industries.create_index("slug", unique=True)
    await db.countries.create_index("code", unique=True)
    await db.feature_flags.create_index("key", unique=True)
    await db.services.create_index([("tenant_id", 1), ("id", 1)])
    await db.customers.create_index([("tenant_id", 1), ("id", 1)])
    await db.leads.create_index([("tenant_id", 1), ("id", 1)])
    await db.appointments.create_index([("tenant_id", 1), ("start_at", 1)])
    await db.knowledge.create_index([("tenant_id", 1), ("id", 1)])
    await db.password_reset_tokens.create_index("expires_at", expireAfterSeconds=0)
    await db.login_attempts.create_index("identifier")
    await db.audit_logs.create_index([("tenant_id", 1), ("timestamp", -1)])
    # Phase 2
    await db.conversations.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.conv_messages.create_index([("conversation_id", 1), ("created_at", 1)])
    await db.estimates.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.invoices.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.review_requests.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.review_requests.create_index("public_token", unique=True)
    await db.invitations.create_index("token", unique=True)
    await db.invitations.create_index([("tenant_id", 1)])
    await db.domains.create_index("domain", unique=True)
    await db.tenant_integrations.create_index([("tenant_id", 1), ("key", 1)], unique=True)
    await db.tenant_automations.create_index("tenant_id", unique=True)
    await db.usage_events.create_index([("tenant_id", 1), ("metric", 1), ("period_key", 1)])
    await db.portal_tokens.create_index("token", unique=True)
    await db.portal_tokens.create_index("expires_at", expireAfterSeconds=0)
    # Phase 3
    await db.appointment_types.create_index([("tenant_id", 1)])
    await db.schedule_staff.create_index([("tenant_id", 1)])
    await db.knowledge_docs.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.knowledge_chunks.create_index([("tenant_id", 1), ("doc_id", 1)])
    await db.automation_rules.create_index([("tenant_id", 1)])
    await db.quality_flags.create_index([("tenant_id", 1), ("status", 1), ("created_at", -1)])
    await db.quality_flags.create_index([("tenant_id", 1), ("conversation_id", 1), ("issue_type", 1)], unique=True)
    await db.industry_briefs.create_index([("tenant_id", 1), ("created_at", -1)])
    await db.user_sessions.create_index("session_token", unique=True)
    await db.user_sessions.create_index("user_id")
    await db.plans.create_index("key", unique=True)
    await db.cost_config.create_index("id", unique=True)
    await db.domain_provider_config.create_index("id", unique=True)


async def run_all_seeds():
    await ensure_indexes()
    try:
        await seed_admin()
    except Exception:  # EMP-W-CF-027: an admin-seed problem must not stop industries/countries/flags/plans
        import logging
        logging.getLogger("seed").exception("platform admin seed failed; continuing with the other seeds")
    await seed_industries()
    await seed_countries()
    await seed_flags()
