"""Read-model shaping for the API (keeps ORM objects out of responses)."""
from __future__ import annotations

from app.models import Document, IntegrityCheck, Transaction
from app.services import sla


def check_out(c: IntegrityCheck) -> dict:
    return {"id": c.id, "stage": c.stage, "passed": c.passed, "detail": c.detail, "expected_sha256": c.expected_sha256,
            "actual_sha256": c.actual_sha256, "blob_meta_sha256": c.blob_meta_sha256, "checked_at": c.checked_at.isoformat()}


def document_out(d: Document, with_checks: bool = False) -> dict:
    out = {
        "id": str(d.id), "transaction_id": str(d.transaction_id), "dcn": d.transaction.dcn if d.transaction else None,
        "parent_document_id": str(d.parent_document_id) if d.parent_document_id else None,
        "artifact_kind": d.artifact_kind.value, "requirement_code": d.requirement_code,
        "filename": d.original_filename, "content_type": d.content_type, "size_bytes": d.size_bytes,
        "sha256": d.sha256, "client_sha256": d.client_sha256, "integrity_state": d.integrity_state.value,
        "doc_class": d.doc_class, "confidence": d.classification_confidence, "extracted": d.extracted or {},
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }
    if with_checks:
        out["integrity_checks"] = [check_out(c) for c in d.integrity_checks]
    return out


def txn_summary(t: Transaction) -> dict:
    s = sla.compute_status(t)
    return {
        "id": str(t.id), "dcn": t.dcn, "request_type": t.request_type, "service_category": t.service_category,
        "priority": t.priority, "status": t.status.value, "provider_npi": t.provider_npi,
        "member_token": t.member.member_token, "source_received_at": t.source_received_at.isoformat(),
        "document_count": sum(1 for d in t.documents if d.parent_document_id is None),
        "sla": s.dict(), "quarantine_reason": t.quarantine_reason,
    }


def txn_detail(t: Transaction) -> dict:
    out = txn_summary(t)
    out.update({
        "answers": t.answers,
        "timestamps": {"source_received_at": t.source_received_at.isoformat(), "cip_received_at": t.cip_received_at.isoformat(),
                       "created_at": t.created_at.isoformat(), "sla_started_at": t.sla_started_at.isoformat(),
                       "completed_at": t.completed_at.isoformat() if t.completed_at else None},
        "documents": [document_out(d, with_checks=True) for d in t.documents],
        "sla_events": [{"event_type": e.event_type, "occurred_at": e.occurred_at.isoformat(), "reason": e.reason,
                        "policy": f"{e.policy_code} v{e.policy_version}"} for e in t.sla_events],
    })
    return out
