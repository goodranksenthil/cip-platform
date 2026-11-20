"""CIP-PA-014 — verify document integrity on every downstream read.

Three-way check: (1) hash recomputed from the stored bytes, (2) hash on the DCN document
record in wip_db, (3) hash in object-storage metadata. All three must agree. A mismatch
quarantines the transaction and blocks downstream clinical processing (fail closed).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Document, IntegrityCheck, IntegrityState, TxnStatus
from app.security.hashing import digests_equal, sha256_stream
from app.services import outbox
from app.services.storage import get_store

# Pipeline step numbers referenced in the requirement
STAGES = {
    "INGESTION": 1,
    "CLASSIFY": 4,
    "EXTRACT": 5,
    "CLINICAL_REVIEW": 13,
    "DECISION": 22,
    "ON_DEMAND": 0,
}


class IntegrityFailure(RuntimeError):
    def __init__(self, check: IntegrityCheck):
        self.check = check
        super().__init__(check.detail)


def verify(db: Session, doc: Document, stage: str, *, raise_on_fail: bool = True) -> IntegrityCheck:
    store = get_store()
    actual: str | None = None
    meta_hash: str | None = None
    detail = "content, DCN record and blob metadata agree"
    try:
        with store.open(doc.blob_path) as fh:
            actual, _ = sha256_stream(fh)
        meta_hash = store.get_metadata(doc.blob_path).get("sha256")
    except FileNotFoundError:
        detail = "blob missing from object storage"

    passed = bool(actual and meta_hash and digests_equal(actual, doc.sha256) and digests_equal(meta_hash, doc.sha256))
    if actual and not passed:
        if not digests_equal(actual, doc.sha256):
            detail = "content hash differs from DCN record — document was modified after ingestion"
        elif not meta_hash:
            detail = "blob metadata hash missing"
        else:
            detail = "blob metadata hash differs from DCN record"

    check = IntegrityCheck(document_id=doc.id, stage=stage, expected_sha256=doc.sha256,
                           actual_sha256=actual, blob_meta_sha256=meta_hash, passed=passed, detail=detail)
    db.add(check)

    if not passed:
        doc.integrity_state = IntegrityState.FAILED
        txn = doc.transaction
        if txn.status != TxnStatus.QUARANTINED:
            txn.status = TxnStatus.QUARANTINED
            txn.quarantine_reason = f"Integrity failure at {stage} on {doc.original_filename}: {detail}"
            outbox.enqueue(db, "IntegrityViolation", "transaction", str(txn.id), dcn=txn.dcn,
                           transaction_id=str(txn.id), document_id=str(doc.id), stage=stage)
        db.flush()
        if raise_on_fail:
            raise IntegrityFailure(check)
    else:
        doc.integrity_state = IntegrityState.VERIFIED
        db.flush()
    return check
