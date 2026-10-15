"""Shared pydantic models used across routers."""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime, timezone
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str
    business_name: Optional[str] = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


# ---------- Industry Template ----------
class IndustryTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=_uuid)
    slug: str
    name: str
    description: str = ""
    icon: str = "briefcase"  # lucide icon name
    ai_personality: str = ""
    knowledge_base: List[Dict[str, str]] = []  # {question,answer}
    services: List[Dict[str, Any]] = []  # {name, description, duration_minutes, price}
    workflows: List[Dict[str, Any]] = []  # {name, steps[]}
    faqs: List[Dict[str, str]] = []
    terminology: Dict[str, str] = {}
    appointment_types: List[str] = []
    intake_questions: List[str] = []
    escalation_rules: List[str] = []
    emergency_rules: List[str] = []
    recommended_integrations: List[str] = []
    recommended_website_content: List[str] = []
    industry_automations: List[str] = []
    active: bool = True
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class IndustryTemplateIn(BaseModel):
    slug: str
    name: str
    description: str = ""
    icon: str = "briefcase"
    ai_personality: str = ""
    knowledge_base: List[Dict[str, str]] = []
    services: List[Dict[str, Any]] = []
    workflows: List[Dict[str, Any]] = []
    faqs: List[Dict[str, str]] = []
    terminology: Dict[str, str] = {}
    appointment_types: List[str] = []
    intake_questions: List[str] = []
    escalation_rules: List[str] = []
    emergency_rules: List[str] = []
    recommended_integrations: List[str] = []
    recommended_website_content: List[str] = []
    industry_automations: List[str] = []
    active: bool = True


# ---------- Tenant / Business ----------
class BusinessHours(BaseModel):
    mon: str = "09:00-17:00"
    tue: str = "09:00-17:00"
    wed: str = "09:00-17:00"
    thu: str = "09:00-17:00"
    fri: str = "09:00-17:00"
    sat: str = "closed"
    sun: str = "closed"


class Address(BaseModel):
    line1: str = ""
    line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "US"


class Branding(BaseModel):
    display_name: str = ""
    primary_color: str = "#0A0A0A"
    accent_color: str = "#2563EB"
    logo_url: str = ""


class AIEmployee(BaseModel):
    name: str = "Alex"
    personality: str = "Warm, professional, concise."
    voice: str = "neutral"
    greeting: str = "Hi! Thanks for calling. How can I help you today?"
    enabled: bool = True


class TenantOnboardingIn(BaseModel):
    name: str
    industry_slug: str
    description: str = ""
    contact_name: str = ""
    contact_email: EmailStr | None = None
    contact_phone: str = ""
    website: str = ""
    social_links: Dict[str, str] = {}
    address: Address = Address()
    service_areas: List[str] = []
    hours: BusinessHours = BusinessHours()
    emergency_hours: str = ""
    emergency_procedures: str = ""
    human_fallback_number: str = ""
    voicemail_prefs: str = "transcribe"
    communication_prefs: List[str] = []  # ["sms","email","voice"]
    services: List[Dict[str, Any]] = []
    staff: List[Dict[str, str]] = []
    faqs: List[Dict[str, str]] = []
    pricing_info: str = ""
    policies: str = ""
    appointment_availability: str = ""


class Tenant(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=_uuid)
    slug: str
    name: str
    industry_slug: str = ""
    description: str = ""
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    website: str = ""
    social_links: Dict[str, str] = {}
    address: Dict[str, Any] = {}
    service_areas: List[str] = []
    hours: Dict[str, Any] = {}
    emergency_hours: str = ""
    emergency_procedures: str = ""
    human_fallback_number: str = ""
    voicemail_prefs: str = "transcribe"
    communication_prefs: List[str] = []
    staff: List[Dict[str, str]] = []
    faqs: List[Dict[str, str]] = []
    pricing_info: str = ""
    policies: str = ""
    appointment_availability: str = ""
    branding: Dict[str, Any] = {}
    ai_employee: Dict[str, Any] = {}
    onboarding_complete: bool = False
    subscription_status: Literal["trial", "active", "past_due", "canceled"] = "trial"
    status: Literal["active", "suspended"] = "active"
    country: str = "US"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


# ---------- Operational resources ----------
class ServiceIn(BaseModel):
    name: str
    description: str = ""
    duration_minutes: int = 60
    price: float = 0.0
    active: bool = True


class Service(ServiceIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    created_at: str = Field(default_factory=_now_iso)


class CustomerIn(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: str = ""
    notes: str = ""
    tags: List[str] = []


class Customer(CustomerIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    created_at: str = Field(default_factory=_now_iso)


class LeadIn(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: str = ""
    source: str = "manual"
    notes: str = ""
    status: Literal["new", "contacted", "qualified", "won", "lost"] = "new"


class Lead(LeadIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    created_at: str = Field(default_factory=_now_iso)


class AppointmentIn(BaseModel):
    customer_name: str
    customer_phone: str = ""
    customer_email: Optional[EmailStr] = None
    customer_id: Optional[str] = None
    lead_id: Optional[str] = None
    service_id: Optional[str] = None
    service_name: str = ""
    staff: str = ""
    start_at: str  # ISO
    end_at: str
    status: Literal["scheduled", "confirmed", "completed", "canceled", "no_show"] = "scheduled"
    notes: str = ""


class Appointment(AppointmentIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    created_at: str = Field(default_factory=_now_iso)


class KnowledgeIn(BaseModel):
    question: str
    answer: str
    tags: List[str] = []


class Knowledge(KnowledgeIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    created_at: str = Field(default_factory=_now_iso)


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    website: Optional[str] = None
    social_links: Optional[Dict[str, str]] = None
    address: Optional[Address] = None
    service_areas: Optional[List[str]] = None
    hours: Optional[BusinessHours] = None
    emergency_hours: Optional[str] = None
    emergency_procedures: Optional[str] = None
    human_fallback_number: Optional[str] = None
    voicemail_prefs: Optional[str] = None
    communication_prefs: Optional[List[str]] = None
    staff: Optional[List[Dict[str, str]]] = None
    faqs: Optional[List[Dict[str, str]]] = None
    pricing_info: Optional[str] = None
    policies: Optional[str] = None
    appointment_availability: Optional[str] = None
    branding: Optional[Branding] = None
    ai_employee: Optional[AIEmployee] = None
    review_url: Optional[str] = None  # Google Business review URL used by review-request flow


# ---------- Country ----------
CountryStatus = Literal["supported", "preview", "extended", "unsupported", "unavailable"]


class CountryIn(BaseModel):
    code: str
    name: str
    status: CountryStatus = "supported"
    enabled: bool = True
    notes: str = ""


class Country(CountryIn):
    id: str = Field(default_factory=_uuid)
    updated_at: str = Field(default_factory=_now_iso)


# ---------- Feature flag ----------
class FeatureFlagIn(BaseModel):
    key: str
    enabled: bool = False
    description: str = ""


class FeatureFlag(FeatureFlagIn):
    id: str = Field(default_factory=_uuid)
    updated_at: str = Field(default_factory=_now_iso)


# ---------- AI chat ----------
class AdvisorMessageIn(BaseModel):
    message: str
    session_id: Optional[str] = None


# ======================================================================
# Domain models (consolidated from models_phase2/3/4.py during cleanup)
# ======================================================================


# ---- Comms: conversations, estimates, invoices, reviews, invitations, domains, usage, portal ----

# ---------- Conversations (calls + SMS) ----------
Channel = Literal["call", "sms", "web_chat"]
ConvStatus = Literal["active", "completed", "escalated", "voicemail", "missed", "failed"]


class ConversationIn(BaseModel):
    channel: Channel = "call"
    caller_name: str = ""
    caller_phone: str = ""
    caller_email: str = ""
    is_simulation: bool = True


class Conversation(ConversationIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    customer_id: Optional[str] = None
    lead_id: Optional[str] = None
    appointment_id: Optional[str] = None
    status: ConvStatus = "active"
    summary: str = ""
    duration_seconds: int = 0
    sentiment: str = ""
    intent: str = ""
    recording_url: str = ""
    created_at: str = Field(default_factory=_now_iso)
    ended_at: Optional[str] = None


class ConvMessageIn(BaseModel):
    text: str


class ConvMessage(BaseModel):
    id: str = Field(default_factory=_uuid)
    conversation_id: str
    tenant_id: str
    role: Literal["caller", "ai", "system", "note"]
    content: str
    action: Optional[Dict[str, Any]] = None  # structured tool call
    created_at: str = Field(default_factory=_now_iso)


# ---------- Estimates ----------
EstimateStatus = Literal["draft", "sent", "approved", "declined", "expired"]


class EstimateLine(BaseModel):
    description: str
    quantity: float = 1
    unit_price: float = 0


class EstimateIn(BaseModel):
    customer_id: Optional[str] = None
    customer_name: str
    customer_phone: str = ""
    title: str
    lines: List[EstimateLine] = []
    notes: str = ""
    expires_at: Optional[str] = None


class Estimate(EstimateIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    total: float = 0
    status: EstimateStatus = "draft"
    public_token: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


# ---------- Invoices ----------
InvoiceStatus = Literal["draft", "sent", "paid", "void", "overdue"]


class InvoiceIn(BaseModel):
    customer_id: Optional[str] = None
    customer_name: str
    customer_phone: str = ""
    title: str
    lines: List[EstimateLine] = []
    notes: str = ""
    due_at: Optional[str] = None


class Invoice(InvoiceIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    total: float = 0
    status: InvoiceStatus = "draft"
    public_token: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now_iso)
    paid_at: Optional[str] = None


# ---------- Review Requests ----------
class ReviewRequestIn(BaseModel):
    customer_id: Optional[str] = None
    customer_name: str
    customer_phone: str = ""
    customer_email: str = ""
    channel: Literal["sms", "email"] = "sms"
    message: str = ""


class ReviewRequest(ReviewRequestIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    public_token: str = Field(default_factory=_uuid)
    status: Literal["sent", "clicked", "responded", "declined"] = "sent"
    rating: Optional[int] = None  # 1-5
    comment: str = ""
    responded_at: Optional[str] = None
    created_at: str = Field(default_factory=_now_iso)


# ---------- Team Invitations ----------
class InvitationIn(BaseModel):
    email: EmailStr
    role: Literal["owner", "admin", "staff"] = "staff"


class Invitation(BaseModel):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    email: str
    role: str
    token: str = Field(default_factory=_uuid)
    invited_by: str
    status: Literal["pending", "accepted", "revoked"] = "pending"
    created_at: str = Field(default_factory=_now_iso)
    accepted_at: Optional[str] = None


class InvitationAccept(BaseModel):
    token: str
    password: str = Field(min_length=8)
    name: str


# ---------- Domains ----------
class DomainIn(BaseModel):
    domain: str  # e.g. www.hearthhvac.com


class Domain(BaseModel):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    domain: str
    status: Literal["pending", "verified", "failed"] = "pending"
    verify_token: str = Field(default_factory=lambda: _uuid()[:12])
    cname_target: str = ""
    last_checked_at: Optional[str] = None
    created_at: str = Field(default_factory=_now_iso)


# ---------- Usage metering ----------
class UsageEvent(BaseModel):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    metric: str  # "ai_minutes" | "calls" | "sms" | "voice_seconds" | "ai_interactions"
    value: float
    period_key: str  # YYYY-MM
    meta: Dict[str, Any] = {}
    created_at: str = Field(default_factory=_now_iso)


# Default limits per plan
PLAN_LIMITS: Dict[str, Dict[str, float]] = {
    "trial":   {"ai_minutes": 60,    "calls": 50,    "sms": 100,   "ai_interactions": 500},
    "starter": {"ai_minutes": 500,   "calls": 500,   "sms": 1000,  "ai_interactions": 5000},
    "growth":  {"ai_minutes": 2500,  "calls": 2500,  "sms": 5000,  "ai_interactions": 25000},
    "scale":   {"ai_minutes": 10000, "calls": 10000, "sms": 20000, "ai_interactions": 100000},
}

WARNING_THRESHOLDS = [0.70, 0.85, 0.90, 0.95, 1.00]


# ---------- Portal (customer-facing) ----------
class PortalMagicLinkIn(BaseModel):
    phone: str = ""
    email: str = ""


class ServiceRequestIn(BaseModel):
    name: str
    phone: str
    email: str = ""
    service: str = ""
    preferred_time: str = ""
    notes: str = ""


# ---------- Automation settings ----------
class AutomationSettings(BaseModel):
    appointment_reminders: bool = True
    reminder_hours_before: int = 24
    review_request_after_job: bool = True
    missed_call_textback: bool = True
    missed_call_textback_message: str = "Sorry we missed you! Reply here and we'll get right back to you."
    follow_up_leads_hours: int = 24
    reminder_sms_template: str = "Hi {name}, this is a reminder for your {service} appointment on {time}. Reply C to confirm."


# ---------- Integrations ----------
class IntegrationConfigIn(BaseModel):
    key: str  # "twilio" | "gmail" | "google_my_business"
    enabled: bool = False
    config: Dict[str, Any] = {}


class IntegrationConfig(IntegrationConfigIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    status: Literal["disconnected", "connected", "error"] = "disconnected"
    last_checked_at: Optional[str] = None
    updated_at: str = Field(default_factory=_now_iso)


# ---------- Receptionist simulate ----------
class SimulateCallerIn(BaseModel):
    caller_name: str = "Jamie Caller"
    caller_phone: str = "+1 555 010 0199"
    scenario: str = ""  # optional seed intent


# ---- Scheduling, knowledge docs, automation rules, AI quality, industry intel ----

# ---------- Scheduling ----------
class StaffMember(BaseModel):
    id: str = Field(default_factory=_uuid)
    name: str
    email: str = ""
    phone: str = ""
    role: str = "technician"
    service_slugs: List[str] = []  # services they can perform
    weekly_hours: Dict[str, str] = {}  # e.g. {"mon": "09:00-17:00"}
    off_dates: List[str] = []  # ISO dates


class AppointmentTypeIn(BaseModel):
    name: str
    duration_minutes: int = 60
    buffer_minutes: int = 15
    travel_minutes: int = 0
    price: float = 0
    color: str = "#2563EB"
    service_slugs: List[str] = []
    requires_staff: bool = True


class AppointmentType(AppointmentTypeIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    created_at: str = Field(default_factory=_now_iso)


class AvailabilityQuery(BaseModel):
    appointment_type_id: Optional[str] = None
    staff_id: Optional[str] = None
    from_iso: str
    to_iso: str


# ---------- Knowledge Documents ----------
class KnowledgeDoc(BaseModel):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    title: str
    filename: str
    content_type: str
    size_bytes: int
    status: Literal["ready", "processing", "failed"] = "ready"
    chunk_count: int = 0
    tags: List[str] = []
    created_at: str = Field(default_factory=_now_iso)


class KnowledgeChunk(BaseModel):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    doc_id: str
    doc_title: str
    chunk_idx: int
    content: str
    # Optional semantic vector (populated when EMBEDDINGS_PROVIDER is enabled).
    embedding: Optional[List[float]] = None
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None
    embedded_at: Optional[str] = None


# ---------- Automation Rules Engine ----------
Trigger = Literal[
    "missed_call", "new_lead", "appointment_booked", "appointment_approaching",
    "job_completed", "invoice_unpaid", "customer_inactive", "high_value_lead",
    "review_responded", "lead_scored_hot",
]

ActionType = Literal[
    "send_sms", "send_email", "notify_owner", "create_followup",
    "record_task", "mark_lead_followup", "request_review",
]


class AutomationRuleIn(BaseModel):
    name: str
    trigger: Trigger
    enabled: bool = True
    conditions: Dict[str, Any] = {}
    actions: List[Dict[str, Any]] = []  # [{type, params}]


class AutomationRule(AutomationRuleIn):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    last_run_at: Optional[str] = None
    run_count: int = 0
    created_at: str = Field(default_factory=_now_iso)


# ---------- AI Quality ----------
class QualityIssueType:
    HALLUCINATION = "possible_hallucination"
    FAILED_BOOKING = "failed_booking"
    FAILED_TRANSFER = "failed_transfer"
    UNRESOLVED = "unresolved"
    FRUSTRATION = "customer_frustration"
    POLICY_VIOLATION = "policy_violation"
    SHORT = "very_short_call"


class QualityFlag(BaseModel):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    conversation_id: str
    issue_type: str
    severity: Literal["low", "medium", "high"] = "medium"
    message: str
    status: Literal["new", "acknowledged", "resolved"] = "new"
    created_at: str = Field(default_factory=_now_iso)


# ---------- Industry Intelligence cache ----------
class IndustryBrief(BaseModel):
    id: str = Field(default_factory=_uuid)
    tenant_id: str
    industry_slug: str
    content: str
    topics: List[str] = []
    created_at: str = Field(default_factory=_now_iso)


# ---- Plans, cost config, domain providers ----

class PlanIn(BaseModel):
    key: str  # trial | starter | growth | ai_office | high_volume | enterprise | custom
    name: str
    price_cents: int = 0  # 0 = custom / contact sales
    interval: Literal["month", "year", "custom"] = "month"
    limits: Dict[str, float] = {}  # ai_minutes, calls, sms, ai_interactions, locations, users, phone_numbers, personas, integrations
    overage: Dict[str, float] = {}  # cents per unit per metric
    features: List[str] = []
    trial_days: int = 0
    is_public: bool = True
    sort_order: int = 100
    description: str = ""
    stripe_price_id: str = ""  # Stripe recurring price id (price_XXX) used for real subscriptions


class Plan(PlanIn):
    id: str = Field(default_factory=_uuid)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class CostConfigIn(BaseModel):
    """Admin-set unit costs for gross-margin math (cents per unit)."""
    costs: Dict[str, float] = {}  # ai_minutes:8, calls:2, sms:0.5, storage_gb:1, payment_processing_bps:290


class CostConfig(CostConfigIn):
    id: str = Field(default="singleton")
    updated_at: str = Field(default_factory=_now_iso)


class DomainSearchIn(BaseModel):
    query: str


class DomainPurchaseIn(BaseModel):
    domain: str
    years: int = 1
    provider: Optional[str] = None  # default from admin config
