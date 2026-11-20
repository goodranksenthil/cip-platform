"""Dashboard, WIP pipeline, events, security, AI gateway, data quality and requirements APIs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.serializers import txn_summary
from app.core.db import get_db
from app.models import (
    AccessAudit, AiAudit, BrokerMessage, Document, DuplicateDelivery, IntegrityCheck, Member, OutboxEvent,
    OutboxStatus, ProcessedEvent, Transaction, TxnStatus,
)
from app.security import field_crypto, phi_guard, prompt_guard
from app.services import ai_gateway, consumers, data_quality, sla
from app.services.broker import get_broker
from app.services.requirements_catalog import REQUIREMENTS
from app.workers import drain

router = APIRouter(prefix="/api")

OPEN_LOAD = (selectinload(Transaction.sla_events), selectinload(Transaction.sla_policy),
             selectinload(Transaction.member), selectinload(Transaction.documents))


# ============================================================================ dashboard
@router.get("/dashboard", tags=["Dashboard"])
def dashboard(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    txns = db.scalars(select(Transaction).options(*OPEN_LOAD)).all()
    statuses = [sla.compute_status(t, now) for t in txns]
    sla_counts: dict[str, int] = {}
    for s in statuses:
        sla_counts[s.state] = sla_counts.get(s.state, 0) + 1

    start = (now - timedelta(days=13)).date()
    daily = {start + timedelta(days=i): {"standard": 0, "expedited": 0} for i in range(14)}
    for t in txns:
        d = t.source_received_at.date()
        if d in daily:
            daily[d]["expedited" if t.priority == "EXPEDITED" else "standard"] += 1

    checks_total = db.scalar(select(func.count()).select_from(IntegrityCheck)) or 0
    checks_passed = db.scalar(select(func.count()).select_from(IntegrityCheck).where(IntegrityCheck.passed)) or 0
    doc_classes = db.execute(select(Document.doc_class, func.count()).where(Document.parent_document_id.is_(None))
                             .group_by(Document.doc_class)).all()
    backlog = db.scalar(select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == OutboxStatus.PENDING))
    dq = data_quality.latest(db)
    recent = sorted(txns, key=lambda t: t.source_received_at, reverse=True)[:8]
    at_risk = sorted([(t, s) for t, s in zip(txns, statuses) if s.state in ("AT_RISK", "BREACHED")],
                     key=lambda x: -x[1].percent_used)[:6]

    return {
        "kpis": {
            "transactions": len(txns),
            "open": sum(1 for t in txns if t.status not in (TxnStatus.COMPLETED,)),
            "documents": db.scalar(select(func.count()).select_from(Document).where(Document.parent_document_id.is_(None))),
            "integrity_pass_rate": round(100 * checks_passed / checks_total, 2) if checks_total else 100.0,
            "integrity_checks": checks_total,
            "quarantined": sum(1 for t in txns if t.status == TxnStatus.QUARANTINED),
            "sla_at_risk": sla_counts.get("AT_RISK", 0), "sla_breached": sla_counts.get("BREACHED", 0),
            "sla_met_rate": round(100 * sla_counts.get("MET", 0) / max(1, sla_counts.get("MET", 0) + sla_counts.get("MISSED", 0)), 1),
            "outbox_backlog": backlog,
            "events_processed": db.scalar(select(func.count()).select_from(ProcessedEvent)),
            "dq_score": dq["overall_score"],
        },
        "daily_intake": [{"date": d.isoformat(), **v} for d, v in daily.items()],
        "status_breakdown": [{"status": s.value, "count": sum(1 for t in txns if t.status == s)} for s in TxnStatus],
        "sla_breakdown": [{"state": k, "count": v} for k, v in sorted(sla_counts.items())],
        "doc_classes": [{"doc_class": c or "PENDING", "count": n} for c, n in doc_classes],
        "request_mix": [{"request_type": rt, "count": sum(1 for t in txns if t.request_type == rt)}
                        for rt in sorted({t.request_type for t in txns})],
        "recent": [txn_summary(t) for t in recent],
        "at_risk": [txn_summary(t) for t, _ in at_risk],
        "dq_dimensions": dq["dimensions"],
    }


# ============================================================================ pipeline
@router.get("/pipeline/outbox", tags=["WIP Pipeline"])
def outbox_state(db: Session = Depends(get_db), limit: int = 50):
    counts = dict(db.execute(select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)).all())
    rows = db.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(limit)).all()
    lat = db.scalar(select(func.avg(func.extract("epoch", OutboxEvent.published_at - OutboxEvent.created_at)))
                    .where(OutboxEvent.published_at.is_not(None)))
    return {
        "broker": get_broker().name,
        "counts": {s.value: counts.get(s, 0) for s in OutboxStatus},
        "avg_publish_latency_s": round(float(lat), 3) if lat else None,
        "events": [{"id": str(e.id), "event_type": e.event_type, "topic": e.topic, "aggregate_id": e.aggregate_id,
                    "status": e.status.value, "attempts": e.attempts, "last_error": e.last_error,
                    "created_at": e.created_at.isoformat(),
                    "published_at": e.published_at.isoformat() if e.published_at else None} for e in rows],
    }


@router.post("/pipeline/run", tags=["WIP Pipeline"])
def run_pipeline():
    return drain(max_rounds=10)


@router.get("/pipeline/sla", tags=["WIP Pipeline"])
def sla_board(db: Session = Depends(get_db)):
    txns = db.scalars(select(Transaction).options(*OPEN_LOAD).where(Transaction.status != TxnStatus.COMPLETED)
                      .order_by(Transaction.source_received_at)).all()
    items = sorted((txn_summary(t) for t in txns), key=lambda x: -x["sla"]["percent_used"])
    return {"items": items, "policies": [
        {"code": p.code, "version": p.version, "target_hours": p.target_hours, "pausable": p.pausable,
         "description": p.description} for p in db.scalars(select(sla.SlaPolicy).order_by(sla.SlaPolicy.code))]}


# ============================================================================ events
@router.get("/events/log", tags=["Event Driven Architecture"])
def event_log(db: Session = Depends(get_db), topic: str | None = None, limit: int = 60):
    stmt = select(BrokerMessage)
    if topic:
        stmt = stmt.where(BrokerMessage.topic == topic)
    rows = db.scalars(stmt.order_by(BrokerMessage.offset.desc()).limit(limit)).all()
    topics = db.execute(select(BrokerMessage.topic, func.count()).group_by(BrokerMessage.topic)).all()
    return {"topics": [{"topic": t, "messages": n} for t, n in topics],
            "messages": [{"offset": m.offset, "topic": m.topic, "key": m.key, "event_id": m.event_id,
                          "event_type": m.event_type, "payload": m.payload, "published_at": m.published_at.isoformat()}
                         for m in rows]}


@router.get("/events/consumers", tags=["Event Driven Architecture"])
def consumer_groups(db: Session = Depends(get_db)):
    dups = db.scalars(select(DuplicateDelivery).order_by(DuplicateDelivery.detected_at.desc()).limit(20)).all()
    outcomes = dict(db.execute(select(ProcessedEvent.outcome, func.count()).group_by(ProcessedEvent.outcome)).all())
    return {"groups": consumers.consumer_lag(db), "processed": outcomes,
            "duplicates": [{"event_id": d.event_id, "consumer_group": d.consumer_group,
                            "detected_at": d.detected_at.isoformat()} for d in dups]}


@router.post("/events/{offset}/replay", tags=["Event Driven Architecture"])
def replay(offset: int, db: Session = Depends(get_db)):
    """Re-deliver an already-processed message (same event_id) — proves idempotent consumers."""
    m = db.get(BrokerMessage, offset)
    if not m:
        raise HTTPException(404, "Offset not found")
    db.add(BrokerMessage(topic=m.topic, key=m.key, event_id=m.event_id, event_type=m.event_type, payload=m.payload))
    db.commit()
    result = consumers.consume_once()
    return {"redelivered_event_id": m.event_id, "consumer_result": result}


# ============================================================================ data security
@router.get("/security/members", tags=["Data Security"])
def members(db: Session = Depends(get_db), limit: int = 25):
    rows = db.scalars(select(Member).order_by(Member.created_at.desc()).limit(limit)).all()
    out = []
    for m in rows:
        out.append({"member_token": m.member_token, "created_at": m.created_at.isoformat(),
                    **field_crypto.ciphertext_preview(db, m),
                    "transactions": db.scalar(select(func.count()).select_from(Transaction).where(Transaction.member_id == m.id))})
    return out


class RevealIn(BaseModel):
    role: str
    purpose: str = Field(min_length=5, max_length=200)


@router.post("/security/members/{token}/reveal", tags=["Data Security"])
def reveal(token: str, body: RevealIn, db: Session = Depends(get_db)):
    m = db.scalars(select(Member).where(Member.member_token == token)).first()
    if not m:
        raise HTTPException(404, "Member not found")
    return {"role": body.role, **field_crypto.decrypt_member(db, m, body.role, body.purpose)}


@router.get("/security/audit", tags=["Data Security"])
def access_audit(db: Session = Depends(get_db)):
    rows = db.scalars(select(AccessAudit).order_by(AccessAudit.at.desc()).limit(30)).all()
    return [{"actor_role": a.actor_role, "action": a.action, "resource": a.resource, "allowed": a.allowed,
             "purpose": a.purpose, "at": a.at.isoformat()} for a in rows]


@router.get("/security/phi-allowlist", tags=["Data Security"])
def phi_allowlist():
    return {"allowlist": {k: sorted(v) for k, v in phi_guard.EVENT_ALLOWLIST.items()},
            "patterns": list(phi_guard.PHI_PATTERNS), "decrypt_roles": sorted(field_crypto.ROLES_ALLOWED_TO_DECRYPT)}


class PhiCheckIn(BaseModel):
    event_type: str
    payload: dict[str, Any]


@router.post("/security/phi-check", tags=["Data Security"])
def phi_check(body: PhiCheckIn):
    problems = phi_guard.inspect_payload(body.event_type, body.payload)
    return {"allowed": not problems, "problems": problems}


# ============================================================================ AI security
class SanitizeIn(BaseModel):
    text: str = Field(max_length=50_000)


@router.post("/ai/sanitize", tags=["Application Security"])
def sanitize(body: SanitizeIn):
    r = prompt_guard.sanitize_untrusted(body.text)
    return {"sanitized": r.text, "original_length": r.original_length, "final_length": len(r.text),
            "truncated": r.truncated, "flags": r.flags, "risk": r.risk, "removed_invisible": r.removed_invisible,
            "phi_detected": phi_guard.scan_text(body.text)}


@router.get("/ai/capabilities", tags=["Application Security"])
def capabilities():
    return {k: {"description": v["description"], "allowed_fields": sorted(v["allowed_fields"]),
                "untrusted_fields": sorted(v["untrusted_fields"])} for k, v in prompt_guard.AI_CAPABILITIES.items()}


class PlaygroundIn(BaseModel):
    capability: str
    candidate: dict[str, Any]


@router.post("/ai/playground", tags=["Application Security"])
def playground(body: PlaygroundIn, db: Session = Depends(get_db)):
    if body.capability not in prompt_guard.AI_CAPABILITIES:
        raise HTTPException(400, "Unknown capability")
    a = ai_gateway.run_capability(db, body.capability, body.candidate)
    return {"response": a.response, "context_sent": a.context_sent, "fields_blocked": a.fields_blocked,
            "injection_flags": a.injection_flags, "blocked": a.blocked, "truncated": a.truncated}


@router.get("/ai/audit", tags=["Application Security"])
def ai_audit(db: Session = Depends(get_db)):
    rows = db.scalars(select(AiAudit).order_by(AiAudit.at.desc()).limit(25)).all()
    return [{"id": a.id, "capability": a.capability, "dcn": a.transaction_dcn, "blocked": a.blocked,
             "fields_blocked": a.fields_blocked, "injection_flags": a.injection_flags, "truncated": a.truncated,
             "at": a.at.isoformat()} for a in rows]


# ============================================================================ data quality
@router.get("/data-quality", tags=["Data Quality"])
def dq(db: Session = Depends(get_db)):
    return {**data_quality.latest(db), "history": data_quality.history(db)}


@router.post("/data-quality/run", tags=["Data Quality"])
def dq_run(db: Session = Depends(get_db)):
    data_quality.run(db)
    return {**data_quality.latest(db), "history": data_quality.history(db)}


# ============================================================================ meta
@router.get("/requirements", tags=["Meta"])
def requirements():
    return REQUIREMENTS


@router.get("/health", tags=["Meta"])
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok", "broker": get_broker().name, "time": datetime.now(timezone.utc).isoformat()}
