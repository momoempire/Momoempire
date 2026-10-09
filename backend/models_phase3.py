"""Phase 3 models: scheduling, knowledge docs, automation rules, AI quality, industry intel."""
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal
from models import _uuid, _now_iso


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
