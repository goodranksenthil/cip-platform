"""Document ingestion use-case (CIP-PA-003, 005, 025, 274, 275).

Order of operations is deliberate:
1. Validate answers against the questionnaire (server is the authority).
2. Stream every file to object storage, hashing in 64KB chunks *before* any transformation.
3. Verify magic bytes and the client-side SHA-256 (end-to-end transfer integrity).
4. ONE database transaction: member (pgcrypto), master transaction + DCN, SLA START,
   document records + hash, ingestion integrity check, PHI-free outbox event.
5. Blob metadata written with the same hash. On any DB failure, uploaded blobs are removed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import BinaryIO

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ArtifactKind, Document, IntegrityCheck, IntegrityState, Member, Transaction, TxnStatus
from app.security.field_crypto import encrypt_expr
from app.security.hashing import digests_equal, is_valid_digest
from app.services import outbox, questionnaire, sla
from app.services.storage import StoredBlob, UploadTooLarge, get_store, sniff_content_type


@dataclass
class IncomingFile:
    requirement_code: str
    filename: str
    stream: BinaryIO
    client_sha256: str | None = None


@dataclass
class IngestionError(Exception):
    message: str
    field_errors: dict[str, str] = field(default_factory=dict)
    status_code: int = 422


def member_token(member_id: str) -> str:
    """Stable pseudonymous token — lets events correlate a member without carrying PHI."""
    key = get_settings().field_encryption_key.encode()
    return "MBR-" + hashlib.blake2b(member_id.upper().encode(), key=key[:64], digest_size=10).hexdigest()


def next_dcn(db: Session, at: datetime) -> str:
    seq = db.execute(text("SELECT nextval('dcn_seq')")).scalar_one()
    return f"DCN-{at:%Y%m%d}-{seq:06d}"


def ingest(db: Session, raw_answers: dict, files: list[IncomingFile],
           source_received_at: datetime | None = None, accept_mime: set[str] | None = None) -> Transaction:
    # ---------------------------------------------------------------- 1. validate
    answers, errors = questionnaire.validate_answers(raw_answers)
    doc_specs = {d["code"]: d for d in questionnaire.required_documents(answers)}
    by_code: dict[str, list[IncomingFile]] = {}
    for f in files:
        if f.requirement_code not in doc_specs:
            errors[f"doc:{f.requirement_code}"] = "This document type is not applicable to the answers given"
        by_code.setdefault(f.requirement_code, []).append(f)
    for code, spec in doc_specs.items():
        n = len(by_code.get(code, []))
        if spec["required"] and n == 0:
            errors[f"doc:{code}"] = f"{spec['label']} is required"
        if n > spec.get("max_files", 5):
            errors[f"doc:{code}"] = f"At most {spec['max_files']} file(s)"
    if errors:
        raise IngestionError("Validation failed", errors)

    # ---------------------------------------------------------------- 2-3. stream + hash
    store = get_store()
    stored: list[tuple[IncomingFile, StoredBlob, str]] = []
    try:
        for f in files:
            try:
                blob = store.put_stream(f.stream, f.filename)
            except UploadTooLarge as e:
                raise IngestionError(str(e), {f"doc:{f.requirement_code}": str(e)}, 413)
            mime = sniff_content_type(blob.head, f.filename)
            allowed = accept_mime or set(questionnaire.DEFINITION["accept"]["mime"])
            if mime not in allowed:
                store.delete(blob.path)
                raise IngestionError("Unsupported file", {f"doc:{f.requirement_code}":
                                     f"{f.filename}: content is not PDF, PNG, JPEG or plain text" if accept_mime is None
                                     else f"{f.filename}: content is not PDF, Word (.docx) or plain text"}, 415)
            if f.client_sha256:
                if not is_valid_digest(f.client_sha256) or not digests_equal(f.client_sha256, blob.sha256):
                    store.delete(blob.path)
                    raise IngestionError("Transfer integrity failure", {f"doc:{f.requirement_code}":
                                         f"{f.filename}: client SHA-256 does not match bytes received"}, 409)
            stored.append((f, blob, mime))

        # ------------------------------------------------------------ 4. one DB transaction
        now = datetime.now(timezone.utc)
        src = source_received_at or now
        priority = questionnaire.derive_priority(answers)
        policy = sla.resolve_policy(db, answers["request_type"], priority)

        mid = answers["member_id"]
        member = db.query(Member).filter_by(member_token=member_token(mid)).one_or_none()
        if member is None:
            member = Member(
                member_token=member_token(mid),
                member_name_enc=encrypt_expr(answers["member_name"]),
                member_dob_enc=encrypt_expr(str(answers["member_dob"])),
                member_addr_enc=encrypt_expr(answers["member_address"]),
                member_id_enc=encrypt_expr(mid),
            )
            db.add(member)
            db.flush()

        non_phi = {k: v for k, v in answers.items() if k not in questionnaire.PHI_QUESTION_IDS}
        txn = Transaction(
            dcn=next_dcn(db, src), request_type=answers["request_type"],
            service_category=answers["service_category"], priority=priority, status=TxnStatus.RECEIVED,
            provider_npi=answers["provider_npi"], answers=non_phi, member_id=member.id,
            source_received_at=src, cip_received_at=src if source_received_at else now,
            created_at=src if source_received_at else now, sla_started_at=src, sla_policy_id=policy.id,
        )
        txn.sla_policy = policy
        db.add(txn)
        db.flush()
        sla.record_event(db, txn, "START", at=src, reason=f"Started on {policy.start_event}")

        docs: list[Document] = []
        for f, blob, mime in stored:
            doc = Document(transaction_id=txn.id, artifact_kind=ArtifactKind.SOURCE, requirement_code=f.requirement_code,
                           original_filename=f.filename, content_type=mime, size_bytes=blob.size, sha256=blob.sha256,
                           client_sha256=f.client_sha256, blob_path=blob.path, integrity_state=IntegrityState.VERIFIED,
                           created_at=src)
            db.add(doc)
            docs.append(doc)
        db.flush()
        for doc in docs:
            db.add(IntegrityCheck(document_id=doc.id, stage="INGESTION", expected_sha256=doc.sha256,
                                  actual_sha256=doc.sha256, blob_meta_sha256=doc.sha256, passed=True,
                                  detail="fingerprint computed at ingestion (pre-transformation)", checked_at=src))
            # 5. same hash on object-storage metadata
            store.set_metadata(doc.blob_path, {"sha256": doc.sha256, "dcn": txn.dcn, "document_id": doc.id,
                                               "artifact_kind": "SOURCE"})

        outbox.enqueue(db, "DocumentIngested", "transaction", str(txn.id), dcn=txn.dcn,
                       transaction_id=str(txn.id), document_ids=[str(d.id) for d in docs],
                       request_type=txn.request_type, service_category=txn.service_category,
                       priority=priority, member_token=member.member_token, document_count=len(docs))
        outbox.enqueue(db, "SlaStateChanged", "transaction", str(txn.id), dcn=txn.dcn, transaction_id=str(txn.id),
                       sla_event="START", policy_code=policy.code, policy_version=policy.version)
        db.commit()
        db.refresh(txn)
        return txn
    except Exception:
        db.rollback()
        for _, blob, _ in stored:
            # content-addressed: only remove if no committed document references it
            if not db.query(Document.id).filter_by(blob_path=blob.path).first():
                store.delete(blob.path)
        raise
