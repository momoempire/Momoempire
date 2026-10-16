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
from routers.two_factor import router as two_factor_router
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
from routers.knowledge_docs import router as knowledge_docs_router, admin_router as knowledge_admin_router
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
from routers.dispatch import router as dispatch_router
from routers.pricebook import router as pricebook_router
from routers.equipment import router as equipment_router
from routers.tracking import router as tracking_router, public_router as tracking_public_router
from routers.financing import router as financing_router, public_router as financing_public_router
from routers.bookkeeping_link import router as bookkeeping_link_router
from routers.photo_estimates import router as photo_estimates_router
from routers.marketing_automation import (
    router as marketing_automation_router,
    public_router as marketing_automation_public_router,
    admin_router as marketing_automation_admin_router,
)
from routers.ai_dispatcher import router as ai_dispatcher_router
from routers.ai_onboarding import router as ai_onboarding_router
from routers.predictive_revenue import router as predictive_revenue_router
from routers.outbound_voice import router as outbound_voice_router
from routers.job_profitability import router as job_profitability_router
from routers.price_intelligence import router as price_intelligence_router
from routers.call_coaching import router as call_coaching_router
from routers.warranty import router as warranty_router
from routers.supplier_pricing import router as supplier_pricing_router
from routers.triage import router as triage_router, public_router as triage_public_router
from routers.subcontractors import router as subcontractors_router
from routers.time_tracking import router as time_tracking_router
from routers.inventory import router as inventory_router
from routers.locations import router as locations_router
from routers.developer import router as developer_router, public_api_router
from routers.trade_packs import router as trade_packs_router
from routers.checklists import router as checklists_router
from routers.home_hub import router as home_hub_router
from routers.video_estimates import router as video_estimates_router
from routers.parts_ordering import router as parts_ordering_router
from routers.ai_collections import router as ai_collections_router
from routers.maintenance_autopilot import router as maintenance_router
from routers.gamification import router as gamification_router
from routers.zero_touch import router as zero_touch_router
from routers.dynamic_pricing import router as dynamic_pricing_router
from routers.self_healing import router as self_healing_router
from routers.cash_flow import router as cash_flow_router
from routers.compliance import router as compliance_router
from routers.reputation import router as reputation_router
from routers.failure_alerts import router as failure_alerts_router
from routers.home_reports import router as home_reports_router
from routers.home_profiles import router as home_profiles_router
from routers.support_tickets import router as support_tickets_router
from routers.platform_status import router as platform_status_router
from routers.support_ai import router as support_ai_router
from routers.inbox import router as inbox_router
from routers.conversation_intel import router as conversation_intel_router
from routers.outreach import router as outreach_router
from routers.satisfaction import router as satisfaction_router
from routers.service_hub import router as service_hub_router

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
        from routers.marketing import waitlist_index_healthy  # EMP-WL-040: no unique email index
        if not waitlist_index_healthy():
            return {"status": "degraded"}
        return {"status": "ok"}
    except Exception:
        logging.getLogger("aio").exception("health: database ping failed")
        return {"status": "degraded", "detail": "service unavailable"}


# Register feature routers
api.include_router(auth_router)
api.include_router(two_factor_router)
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
api.include_router(knowledge_admin_router)
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
api.include_router(dispatch_router)
api.include_router(pricebook_router)
api.include_router(equipment_router)
api.include_router(tracking_router)
api.include_router(tracking_public_router)
api.include_router(financing_router)
api.include_router(financing_public_router)
api.include_router(bookkeeping_link_router)
api.include_router(photo_estimates_router)
api.include_router(marketing_automation_router)
api.include_router(marketing_automation_public_router)
api.include_router(marketing_automation_admin_router)
api.include_router(job_profitability_router)
api.include_router(price_intelligence_router)
api.include_router(ai_dispatcher_router)
api.include_router(ai_onboarding_router)
api.include_router(predictive_revenue_router)
api.include_router(outbound_voice_router)
api.include_router(call_coaching_router)
api.include_router(warranty_router)
api.include_router(supplier_pricing_router)
api.include_router(triage_router)
api.include_router(triage_public_router)
api.include_router(subcontractors_router)
api.include_router(time_tracking_router)
api.include_router(inventory_router)
api.include_router(locations_router)
api.include_router(trade_packs_router)
api.include_router(developer_router)
api.include_router(checklists_router)
api.include_router(parts_ordering_router)
api.include_router(maintenance_router)
app.include_router(home_hub_router)
app.include_router(video_estimates_router)
app.include_router(ai_collections_router)
app.include_router(gamification_router)
api.include_router(dynamic_pricing_router)
api.include_router(self_healing_router)
api.include_router(cash_flow_router)
app.include_router(zero_touch_router)
app.include_router(compliance_router)
app.include_router(reputation_router)
app.include_router(failure_alerts_router)
app.include_router(home_reports_router)
app.include_router(home_profiles_router)
app.include_router(support_tickets_router)
app.include_router(platform_status_router)
app.include_router(support_ai_router)
app.include_router(inbox_router)
app.include_router(conversation_intel_router)
app.include_router(outreach_router)
app.include_router(satisfaction_router)
app.include_router(service_hub_router)
app.include_router(public_api_router)
# Stripe is registered to deliver webhooks to /api/stripe/webhook (top-level, platform account).
api.add_api_route("/stripe/webhook", _stripe_wh, methods=["POST"], include_in_schema=False)
# Stripe Connect webhook (connected accounts) → /api/stripe/connect-webhook. Separate signing secret.
api.add_api_route("/stripe/connect-webhook", _c2p_connect_wh, methods=["POST"], include_in_schema=False)

app.include_router(api)


# CORS: strict allow-list from CORS_ORIGINS via deploy_security (no regex fallback).
# APP_ENV=production|staging with no valid allow-list fails closed (no cross-origin) and logs an error.
from deploy_security import cors_options  # noqa: E402
app.add_middleware(CORSMiddleware, **cors_options())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# Error monitoring: no-op unless SENTRY_DSN is set.
from sentry_init import init_sentry  # noqa: E402
init_sentry()
log = logging.getLogger("aio")

# EMP-W-CF-023: cookie-authenticated POST/PUT/PATCH/DELETE must come from an allowed Origin
# (or Referer). Webhooks, cron and the waitlist are exempt. See csrf_guard.py.
from csrf_guard import CSRFOriginMiddleware  # noqa: E402
app.add_middleware(CSRFOriginMiddleware)


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
