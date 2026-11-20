"""Transactions, documents, integrity, SLA actions and review decisions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.serializers import check_out, document_out, txn_detail, txn_summary
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Document, Transaction, TxnStatus
from app.services import ai_gateway, integrity, outbox, sla
from app.services.storage import get_store

router = APIRouter(prefix="/api", tags=["Transactions & Documents"])

TXN_LOAD = (selectinload(Transaction.documents).selectinload(Document.integrity_checks),
            selectinload(Transaction.sla_events), selectinload(Transaction.sla_policy), selectinload(Transaction.member))


def _txn(db: Session, txn_id: str) -> Transaction:
    try:
        t = db.scalars(select(Transaction).options(*TXN_LOAD).where(Transaction.id == uuid.UUID(txn_id))).first()
    except ValueError:
        t = None
    if not t:
        raise HTTPException(404, "Transaction not found")
    return t


@router.get("/transactions")
def list_transactions(db: Session = Depends(get_db), status: str | None = None, q: str | None = None,
                      request_type: str | None = None, page: int = Query(1, ge=1), size: int = Query(20, le=100)):
    stmt = select(Transaction).options(*TXN_LOAD)
    if status:
        stmt = stmt.where(Transaction.status == TxnStatus(status))
    if request_type:
        stmt = stmt.where(Transaction.request_type == request_type)
    if q:
        stmt = stmt.where(or_(Transaction.dcn.ilike(f"%{q}%"), Transaction.provider_npi.ilike(f"%{q}%")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Transaction.source_received_at.desc()).offset((page - 1) * size).limit(size)).all()
    return {"total": total, "page": page, "size": size, "items": [txn_summary(t) for t in rows]}


@router.get("/transactions/{txn_id}")
def get_transaction(txn_id: str, db: Session = Depends(get_db)):
    return txn_detail(_txn(db, txn_id))


class SlaAction(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/transactions/{txn_id}/sla/{action}")
def sla_action(txn_id: str, action: Literal["pause", "resume"], body: SlaAction, db: Session = Depends(get_db)):
    t = _txn(db, txn_id)
    try:
        ev = sla.record_event(db, t, action.upper(), reason=body.reason)
    except sla.SlaTransitionError as e:
        raise HTTPException(409, str(e))
    outbox.enqueue(db, "SlaStateChanged", "transaction", str(t.id), dcn=t.dcn, transaction_id=str(t.id),
                   sla_event=ev.event_type, policy_code=ev.policy_code, policy_version=ev.policy_version)
    db.commit()
    return txn_detail(_txn(db, txn_id))


class DecisionIn(BaseModel):
    decision: Literal["APPROVED", "DENIED"]


@router.post("/transactions/{txn_id}/decision")
def decide(txn_id: str, body: DecisionIn, db: Session = Depends(get_db)):
    """Steps 13 & 22: integrity must be re-proven immediately before a clinical determination."""
    t = _txn(db, txn_id)
    if t.status == TxnStatus.QUARANTINED:
        raise HTTPException(423, f"Transaction is quarantined: {t.quarantine_reason}")
    if t.status == TxnStatus.COMPLETED:
        raise HTTPException(409, "Determination already issued")
    if t.sla_events and t.sla_events[-1].event_type == "PAUSE":
        raise HTTPException(409, "Resume the SLA clock before issuing a determination")
    try:
        for d in [d for d in t.documents if d.parent_document_id is None]:
            integrity.verify(db, d, "CLINICAL_REVIEW")
            integrity.verify(db, d, "DECISION")
    except integrity.IntegrityFailure as e:
        db.commit()  # persist the failed check + quarantine
        raise HTTPException(423, f"Integrity verification failed — processing blocked: {e.check.detail}")
    sla.record_event(db, t, "COMPLETE", reason=f"Determination: {body.decision}")
    t.status, t.completed_at = TxnStatus.COMPLETED, datetime.now(timezone.utc)
    outbox.enqueue(db, "ReviewDecided", "transaction", str(t.id), dcn=t.dcn, transaction_id=str(t.id), decision=body.decision)
    outbox.enqueue(db, "SlaStateChanged", "transaction", str(t.id), dcn=t.dcn, transaction_id=str(t.id),
                   sla_event="COMPLETE", policy_code=t.sla_policy.code, policy_version=t.sla_policy.version)
    db.commit()
    return txn_detail(_txn(db, txn_id))


class SummaryIn(BaseModel):
    reviewer_note: str = ""


@router.post("/transactions/{txn_id}/ai-summary")
def ai_summary(txn_id: str, body: SummaryIn, db: Session = Depends(get_db)):
    t = _txn(db, txn_id)
    a = ai_gateway.summarise_transaction(db, t, body.reviewer_note)
    return {"response": a.response, "context_sent": a.context_sent, "fields_blocked": a.fields_blocked,
            "injection_flags": a.injection_flags, "blocked": a.blocked, "truncated": a.truncated}


# ---------------------------------------------------------------------------- documents
@router.get("/documents")
def list_documents(db: Session = Depends(get_db), kind: str | None = None, state: str | None = None,
                   doc_class: str | None = None, limit: int = Query(100, le=500)):
    stmt = select(Document).options(selectinload(Document.transaction))
    if kind:
        stmt = stmt.where(Document.artifact_kind == kind)
    if state:
        stmt = stmt.where(Document.integrity_state == state)
    if doc_class:
        stmt = stmt.where(Document.doc_class == doc_class)
    rows = db.scalars(stmt.order_by(Document.created_at.desc()).limit(limit)).all()
    return [document_out(d) for d in rows]


@router.get("/documents/{doc_id}")
def get_document(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    children = db.scalars(select(Document).where(Document.parent_document_id == d.id)).all()
    out = document_out(d, with_checks=True)
    out["children"] = [document_out(c) for c in children]
    out["blob_metadata"] = get_store().get_metadata(d.blob_path)
    return out


@router.post("/documents/{doc_id}/verify")
def verify_document(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    check = integrity.verify(db, d, "ON_DEMAND", raise_on_fail=False)
    db.commit()
    return check_out(check)


@router.post("/documents/{doc_id}/simulate-tamper")
def simulate_tamper(doc_id: uuid.UUID, db: Session = Depends(get_db)):
    """DEMO ONLY (local env): append bytes to the stored blob to prove tamper detection."""
    if get_settings().environment != "local":
        raise HTTPException(403, "Disabled outside local environment")
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404, "Document not found")
    get_store().append(d.blob_path, b"\n[tampered]")
    return {"message": "Blob modified on storage. Run verification to see detection."}
