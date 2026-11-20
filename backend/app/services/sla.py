"""CIP-PA-005 — versioned SLA policies and an append-only SLA event ledger.

The SLA clock is never a mutable "due_at" column. It is *derived* from the immutable
event ledger (START / PAUSE / RESUME / COMPLETE) and the policy version captured at START,
so a later policy change cannot rewrite history for in-flight transactions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SlaEvent, SlaPolicy, Transaction

DEFAULT_POLICIES = [
    # code, description, priority, hours, pausable
    ("PRIOR_AUTH:STANDARD", "Standard prior authorization", "STANDARD", 168, True),
    ("PRIOR_AUTH:EXPEDITED", "Expedited prior authorization", "EXPEDITED", 72, False),
    ("APPEAL:STANDARD", "Standard appeal", "STANDARD", 720, True),
    ("APPEAL:EXPEDITED", "Expedited appeal", "EXPEDITED", 72, False),
    ("RETRO_REVIEW:STANDARD", "Retrospective review", "STANDARD", 720, True),
    ("RETRO_REVIEW:EXPEDITED", "Retrospective review (expedited)", "EXPEDITED", 168, True),
    ("CONCURRENT_REVIEW:STANDARD", "Concurrent review", "STANDARD", 72, True),
    ("CONCURRENT_REVIEW:EXPEDITED", "Concurrent review (expedited)", "EXPEDITED", 24, False),
]

VALID_TRANSITIONS = {
    None: {"START"},
    "START": {"PAUSE", "COMPLETE"},
    "RESUME": {"PAUSE", "COMPLETE"},
    "PAUSE": {"RESUME"},
    "COMPLETE": set(),
}


class SlaTransitionError(ValueError):
    pass


def ensure_policies(db: Session) -> None:
    existing = {p.code for p in db.scalars(select(SlaPolicy))}
    for code, desc, prio, hours, pausable in DEFAULT_POLICIES:
        if code not in existing:
            db.add(SlaPolicy(code=code, version=1, description=desc, priority=prio,
                             target_hours=hours, pausable=pausable))
    db.commit()


def resolve_policy(db: Session, request_type: str, priority: str) -> SlaPolicy:
    code = f"{request_type}:{priority}"
    policy = db.scalars(
        select(SlaPolicy).where(SlaPolicy.code == code, SlaPolicy.is_active.is_(True))
        .order_by(SlaPolicy.version.desc())
    ).first()
    if not policy:
        raise LookupError(f"No active SLA policy for {code}")
    return policy


def record_event(db: Session, txn: Transaction, event_type: str, at: datetime | None = None,
                 reason: str | None = None) -> SlaEvent:
    last = txn.sla_events[-1].event_type if txn.sla_events else None
    if event_type not in VALID_TRANSITIONS.get(last, set()):
        raise SlaTransitionError(f"Cannot {event_type} after {last or 'nothing'}")
    if event_type == "PAUSE" and not txn.sla_policy.pausable:
        raise SlaTransitionError(f"Policy {txn.sla_policy.code} v{txn.sla_policy.version} does not permit pausing")
    ev = SlaEvent(transaction_id=txn.id, event_type=event_type, policy_code=txn.sla_policy.code,
                  policy_version=txn.sla_policy.version, reason=reason,
                  occurred_at=at or datetime.now(timezone.utc))
    db.add(ev)
    txn.sla_events.append(ev)
    return ev


@dataclass
class SlaStatus:
    policy_code: str
    policy_version: int
    target_hours: int
    elapsed_hours: float
    remaining_hours: float
    percent_used: float
    state: str  # ON_TRACK | AT_RISK | BREACHED | PAUSED | MET | MISSED
    projected_due_at: str | None
    paused_hours: float

    def dict(self) -> dict:
        return asdict(self)


def compute_status(txn: Transaction, now: datetime | None = None) -> SlaStatus:
    now = now or datetime.now(timezone.utc)
    active = timedelta()
    paused = timedelta()
    running_since: datetime | None = None
    paused_since: datetime | None = None
    completed_at: datetime | None = None
    for ev in txn.sla_events:
        if ev.event_type in ("START", "RESUME"):
            if paused_since:
                paused += ev.occurred_at - paused_since
                paused_since = None
            running_since = ev.occurred_at
        elif ev.event_type == "PAUSE" and running_since:
            active += ev.occurred_at - running_since
            running_since, paused_since = None, ev.occurred_at
        elif ev.event_type == "COMPLETE":
            if running_since:
                active += ev.occurred_at - running_since
            running_since, completed_at = None, ev.occurred_at
    if running_since:
        active += now - running_since
    if paused_since:
        paused += now - paused_since

    target = timedelta(hours=txn.sla_policy.target_hours)
    remaining = target - active
    pct = round(100 * active / target, 1) if target else 0.0
    if completed_at:
        state = "MET" if active <= target else "MISSED"
    elif paused_since:
        state = "PAUSED"
    elif remaining <= timedelta(0):
        state = "BREACHED"
    elif pct >= 75:
        state = "AT_RISK"
    else:
        state = "ON_TRACK"
    projected = None if (completed_at or paused_since) else (now + remaining).isoformat()
    return SlaStatus(txn.sla_policy.code, txn.sla_policy.version, txn.sla_policy.target_hours,
                     round(active.total_seconds() / 3600, 2), round(remaining.total_seconds() / 3600, 2),
                     pct, state, projected, round(paused.total_seconds() / 3600, 2))
