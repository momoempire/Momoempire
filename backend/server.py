"""AI Office Platform — Phase 1 backend."""
from dotenv import load_dotenv
from pathlib import Path
ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

import os
import logging
from fastapi import FastAPI, APIRouter, Request
from starlette.middleware.cors import CORSMiddleware

from db import get_db, close_db
from seed_data import run_all_seeds
from routers.auth import router as auth_router
from routers.industries import router as industries_router
from routers.countries import router as countries_router
from routers.tenants import router as tenants_router
from routers.admin import router as admin_router
from routers.advisor import router as advisor_router
from routers.public import router as public_router
from routers.payments import router as payments_router, stripe_webhook as _stripe_wh
from routers.conversations import router as conversations_router
from routers.crm import router as crm_router
from routers.reviews import router as reviews_router, public_router as reviews_public_router
from routers.invitations import router as invitations_router, public_router as invitations_public_router
from routers.domains import router as domains_router
from routers.integrations import router as integrations_router
from routers.automations import router as automations_router
from routers.portal import router as portal_router
from routers.usage import router as usage_router
from routers.scheduling import router as scheduling_router
from routers.pipeline import router as pipeline_router
from routers.knowledge_docs import router as knowledge_docs_router
from routers.ai_quality import router as quality_router, admin_router as quality_admin_router
from routers.automation_rules import router as automation_rules_router
from routers.industry_intel import router as industry_intel_router
from routers.plans import router as plans_router, public_router as plans_public_router, seed_plans
from routers.platform_analytics import router as platform_analytics_router
from routers.domain_providers import router as domain_providers_router, admin_router as domain_providers_admin_router
from routers.workspaces import router as workspaces_router
from routers.sales_intel import router as sales_intel_router
from routers.overage import tenant_router as overage_tenant_router, admin_router as overage_admin_router
from routers.source_export import router as source_export_router
from routers.cron import router as cron_router
from routers.twilio_webhook import router as twilio_router
from routers.growth import router as growth_router, public_referrals as public_referrals_router
from routers.sales_extra import router as sales_extra_router, realtime as realtime_router
from routers.sales_expert import router as sales_expert_router
from routers.widget import router as widget_router
from routers.post_job import router as post_job_router
from routers.phase9 import router as phase9_router
from routers.marketing import router as marketing_router
from routers.health_deploy import router as health_deploy_router
from routers.call_to_payment import (
    router as c2p_router,
    public_router as c2p_public_router,
    connect_webhook as _c2p_connect_wh,
)
from routers.repeat import router as repeat_router
from routers.testimonials import router as testimonials_router, public_router as testimonials_public_router

app = FastAPI(title="AI Office Platform API")
api = APIRouter(prefix="/api")


@api.get("/")
async def root():
    return {
        "service": "ai-office-platform",
        "version": "0.1.0",
        "status": "ok",
    }


@api.get("/health")
async def health():
    db = get_db()
    try:
        await db.command("ping")
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        return {"status": "degraded", "detail": str(e)}


# Register feature routers
api.include_router(auth_router)
api.include_router(industries_router)
api.include_router(countries_router)
api.include_router(tenants_router)
api.include_router(admin_router)
api.include_router(advisor_router)
api.include_router(public_router)
api.include_router(payments_router)
api.include_router(conversations_router)
api.include_router(crm_router)
api.include_router(reviews_router)
api.include_router(reviews_public_router)
api.include_router(invitations_router)
api.include_router(invitations_public_router)
api.include_router(domains_router)
api.include_router(integrations_router)
api.include_router(automations_router)
api.include_router(portal_router)
api.include_router(usage_router)
api.include_router(scheduling_router)
api.include_router(pipeline_router)
api.include_router(knowledge_docs_router)
api.include_router(quality_router)
api.include_router(quality_admin_router)
api.include_router(automation_rules_router)
api.include_router(industry_intel_router)
api.include_router(plans_router)
api.include_router(plans_public_router)
api.include_router(platform_analytics_router)
api.include_router(domain_providers_router)
api.include_router(domain_providers_admin_router)
api.include_router(workspaces_router)
api.include_router(sales_intel_router)
api.include_router(overage_tenant_router)
api.include_router(overage_admin_router)
api.include_router(source_export_router)
api.include_router(cron_router)
api.include_router(twilio_router)
api.include_router(growth_router)
api.include_router(public_referrals_router)
api.include_router(sales_extra_router)
api.include_router(realtime_router)
api.include_router(sales_expert_router)
api.include_router(widget_router)
api.include_router(post_job_router)
api.include_router(phase9_router)
api.include_router(marketing_router)
api.include_router(repeat_router)
api.include_router(testimonials_router)
api.include_router(testimonials_public_router)
api.include_router(health_deploy_router)
api.include_router(c2p_router)
api.include_router(c2p_public_router)
# Stripe is registered to deliver webhooks to /api/stripe/webhook (top-level, platform account).
api.add_api_route("/stripe/webhook", _stripe_wh, methods=["POST"], include_in_schema=False)
# Stripe Connect webhook (connected accounts) → /api/stripe/connect-webhook. Separate signing secret.
api.add_api_route("/stripe/connect-webhook", _c2p_connect_wh, methods=["POST"], include_in_schema=False)

app.include_router(api)


# CORS: env-driven allow-list when CORS_ORIGINS is set; otherwise permissive (dev + Emergent preview).
# For Cloudflare Pages / Oracle / Fly, set CORS_ORIGINS="https://your-pages.pages.dev,https://your-domain.com"
_cors_env = os.environ.get("CORS_ORIGINS", "").strip()
_cors_kwargs: dict = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if _cors_env and _cors_env != "*":
    _cors_kwargs["allow_origins"] = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    _cors_kwargs["allow_origin_regex"] = ".*"
app.add_middleware(CORSMiddleware, **_cors_kwargs)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("aio")


@app.on_event("startup")
async def startup():
    try:
        await run_all_seeds()
        await seed_plans()
        # Idempotency index for the Connect webhook — duplicate event.id → safe skip.
        from db import get_db
        _db = get_db()
        try:
            await _db.webhook_events.create_index("_id", unique=True)
        except Exception:
            pass
        try:
            await _db.invoice_payments.create_index("stripe_payment_intent_id", unique=True)
        except Exception:
            pass
        log.info("Seed complete")
    except Exception as e:
        log.exception("Seed failed: %s", e)


@app.on_event("shutdown")
async def shutdown():
    await close_db()


# EMP-WL-029: waitlist POST must be JSON (415) and, with a CORS_ORIGINS allow-list, from an
# allowed Origin (403). Blocks cross-site text/plain signups. Keep when PR #11 merges.
# EMP-WL-036: installed INSIDE CORSMiddleware so its 415/403 replies get CORS headers for allowed
# origins (and none for disallowed ones). Don't switch this to app.add_middleware().
from waitlist_guard import install_waitlist_guard  # noqa: E402
install_waitlist_guard(app)

# EMP-WL-002: WAITLIST_ONLY=true swaps in a minimal app (health + waitlist only; no seeding).
from waitlist_mode import waitlist_only_enabled, build_waitlist_app  # noqa: E402
if waitlist_only_enabled():
    app = build_waitlist_app(app)
