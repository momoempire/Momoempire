"""AI dispatcher scoring engine: deterministic, explainable weighted scoring.

Given unassigned jobs and technicians for a day, this ranks every (job, tech)
pair with a 0-100 score plus human-readable reasons. No live LLM is required;
the score is a pure function of the inputs (same input -> same ranking), which
keeps results reproducible and testable.

TODO(LIVE): an LLM rerank can plug in here — feed it the top-N deterministic
candidates per job, ask it to rerank with its own reasons, and blend the
result with `score_pair()` (e.g. 70% deterministic / 30% LLM). The
deterministic score must always remain the fallback when the model is
unavailable, and suggestions must stay human-approved before applying.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

# Component weights — must sum to 100.
W_SKILL = 40
W_DRIVE = 20
W_SCHEDULE = 20
W_LOAD = 10
W_PROFIT = 10

# Drive scoring: miles that zero out the drive component.
DRIVE_MAX_MILES = 25.0
# Rough urban drive speed used only for the human-readable ETA hint.
DRIVE_MPH = 25.0
# A tech working more than this many minutes on the day is considered overloaded.
OVERLOAD_MINUTES = 480
# Auto-assign only applies suggestions at or above this score.
MIN_AUTO_ASSIGN_SCORE = 50.0


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


# ---------------------------------------------------------------------------
# Input extraction
# ---------------------------------------------------------------------------

def job_skill_key(job: dict) -> str:
    """Best skill identifier for a job: explicit id/slug first, then service name."""
    return (
        job.get("service_id")
        or job.get("service_slug")
        or job.get("service_name")
        or ""
    )


def job_value(job: dict) -> "float | None":
    """Best available revenue figure on the job, or None when unknown."""
    for key in ("price", "quote_total", "estimated_revenue", "total", "amount"):
        val = job.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def _parse_iso(value: str) -> "datetime | None":
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def job_minutes(job: dict) -> int:
    """Duration of a job in whole minutes (60 fallback)."""
    start, end = _parse_iso(job.get("start_at", "")), _parse_iso(job.get("end_at", ""))
    if start and end and end > start:
        return max(1, int((end - start).total_seconds() // 60))
    return 60


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------

def skill_score(job: dict, tech: dict) -> "tuple[float, str]":
    """40 pts: does the tech's service_slugs cover the job's skill?"""
    want = job_skill_key(job)
    have = [s for s in (tech.get("service_slugs") or []) if s]
    name = tech.get("name", "tech")
    if not want:
        return 60.0, f"Job has no service detail — scored neutrally for {name}"
    if not have:
        return 55.0, f"{name} has no listed skills — treated as a generalist"
    want_n = _norm(want)
    have_n = {_norm(s) for s in have}
    # Match on the skill id, or any token of the skill id inside the service name.
    if want_n in have_n or any(h and h in want_n for h in have_n):
        label = want or job.get("service_name") or "this service"
        return 100.0, f"Skill match: {name} is certified for '{label}'"
    label = want or job.get("service_name") or "this service"
    return 15.0, f"Skill mismatch: {name} is not listed for '{label}' — downranked"


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles (same math as routers/tracking)."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def tech_anchor(tech: dict, location: "dict | None") -> "tuple[float, float] | None":
    """Where drive time is measured from: live GPS ping first, then home base."""
    if location:
        lat, lng = location.get("lat"), location.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            return float(lat), float(lng)
    lat, lng = tech.get("home_base_lat"), tech.get("home_base_lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None


def drive_score(job: dict, tech: dict, location: "dict | None") -> "tuple[float, str, float | None]":
    """20 pts: haversine drive estimate from the tech's anchor to the job site."""
    name = tech.get("name", "tech")
    anchor = tech_anchor(tech, location)
    lat, lng = job.get("service_lat"), job.get("service_lng")
    if anchor is None:
        return 50.0, f"No location on file for {name} — drive time unknown", None
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
        return 50.0, f"Job has no site coordinates — drive time unknown for {name}", None
    miles = haversine_miles(anchor[0], anchor[1], float(lat), float(lng))
    mins = int(round(miles / DRIVE_MPH * 60))
    score = max(0.0, 100.0 - (miles / DRIVE_MAX_MILES) * 100.0)
    if miles < 3:
        return score, f"Very close: ~{miles:.1f} mi (~{mins} min drive) for {name}", miles
    if miles <= DRIVE_MAX_MILES:
        return score, f"Drive: ~{miles:.1f} mi (~{mins} min) for {name}", miles
    return score, f"Far: ~{miles:.1f} mi (~{mins} min) — drive penalty for {name}", miles


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


_WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def schedule_score(job: dict, tech: dict, day_jobs: list) -> "tuple[float, str]":
    """20 pts: no time conflict with the tech's existing day, inside work hours."""
    name = tech.get("name", "tech")
    start, end = _parse_iso(job.get("start_at", "")), _parse_iso(job.get("end_at", ""))
    if not start or not end:
        return 50.0, f"Job time unclear — schedule fit unknown for {name}"
    for other in day_jobs:
        o_start, o_end = _parse_iso(other.get("start_at", "")), _parse_iso(other.get("end_at", ""))
        if o_start and o_end and _overlaps(start, end, o_start, o_end):
            other_t = o_start.strftime("%H:%M")
            return 0.0, f"Conflict: {name} is booked {other_t} on another job"
    hours = (tech.get("weekly_hours") or {}).get(_WEEKDAY_KEYS[start.weekday()])
    if hours:
        try:
            open_s, close_s = [p.strip() for p in hours.split("-", 1)]
            open_t = datetime.strptime(open_s, "%H:%M").time()
            close_t = datetime.strptime(close_s, "%H:%M").time()
            if not (open_t <= start.time() and end.time() <= close_t):
                return 20.0, f"Outside {name}'s hours ({hours}) — possible overtime"
            return 100.0, f"Fits {name}'s schedule (hours {hours}, no conflicts)"
        except Exception:
            pass
    return 80.0, f"No conflicts for {name}; work hours not set"


def load_score(day_jobs: list, tech: dict) -> "tuple[float, str]":
    """10 pts: penalize techs already carrying a full day."""
    name = tech.get("name", "tech")
    total = sum(job_minutes(j) for j in day_jobs)
    if total >= OVERLOAD_MINUTES:
        return 0.0, f"{name} is overloaded ({total // 60}h+ booked) — overtime risk"
    if total >= 300:
        return 50.0, f"{name} already has {total // 60}h{total % 60:02d}m booked — heavy day"
    return 100.0, f"{name}'s day is light ({total // 60}h{total % 60:02d}m booked)"


def profitability_score(job: dict) -> "tuple[float, str]":
    """10 pts: hint — prioritize keeping high-value jobs well staffed.

    This component is job-constant (it does not change the tech ranking); it
    surfaces in the reasons so a human approves high-value assignments first.
    """
    val = job_value(job)
    if val is None:
        return 50.0, "No price on the job — profitability hint unavailable"
    if val >= 500:
        return 100.0, f"High-value job (${val:,.0f}) — worth your best tech"
    if val >= 200:
        return 80.0, f"Solid job value (${val:,.0f})"
    return 60.0, f"Standard job value (${val:,.0f})"


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def score_pair(job: dict, tech: dict, location: "dict | None",
               day_jobs: list) -> dict:
    """Score one (job, tech) pair. Pure function — deterministic."""
    reasons = []
    s_skill, r_skill = skill_score(job, tech)
    reasons.append(r_skill)
    s_drive, r_drive, _miles = drive_score(job, tech, location)
    reasons.append(r_drive)
    s_sched, r_sched = schedule_score(job, tech, day_jobs)
    reasons.append(r_sched)
    s_load, r_load = load_score(day_jobs, tech)
    reasons.append(r_load)
    s_profit, r_profit = profitability_score(job)
    reasons.append(r_profit)
    total = (
        W_SKILL * s_skill + W_DRIVE * s_drive + W_SCHEDULE * s_sched
        + W_LOAD * s_load + W_PROFIT * s_profit
    ) / 100.0
    return {
        "job_id": job.get("id"),
        "tech_id": tech.get("id"),
        "tech_name": tech.get("name", ""),
        "score": round(total, 1),
        "reasons": reasons,
    }


def rank_for_job(job: dict, techs: list, locations: dict,
                 jobs_by_tech: dict) -> list:
    """Rank every tech for one job, best first (score desc, then tech id)."""
    ranked = [
        score_pair(job, tech, locations.get(tech.get("id")),
                   [j for j in jobs_by_tech.get(tech.get("id"), []) if j.get("id") != job.get("id")])
        for tech in techs
    ]
    ranked.sort(key=lambda r: (-r["score"], r["tech_id"] or ""))
    return ranked


def suggest_for_day(jobs: list, techs: list, locations: dict,
                    jobs_by_tech: dict) -> list:
    """One ranked candidate list per unassigned job. Pure function."""
    return [
        {"job_id": job.get("id"), "candidates": rank_for_job(job, techs, locations, jobs_by_tech)}
        for job in jobs
    ]
