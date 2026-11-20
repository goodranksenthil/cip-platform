"""ORM models for wip_db. Each table is annotated with the requirement(s) it serves."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TxnStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    CLASSIFIED = "CLASSIFIED"
    EXTRACTED = "EXTRACTED"
    IN_REVIEW = "IN_REVIEW"
    COMPLETED = "COMPLETED"
    QUARANTINED = "QUARANTINED"


class IntegrityState(str, enum.Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ArtifactKind(str, enum.Enum):
    SOURCE = "SOURCE"
    DERIVATIVE_TEXT = "DERIVATIVE_TEXT"


class OutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    DEAD = "DEAD"


# ---------------------------------------------------------------- SLA policy (versioned)
class SlaPolicy(Base):
    __tablename__ = "sla_policies"
    __table_args__ = (UniqueConstraint("code", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(20))  # STANDARD | EXPEDITED
    target_hours: Mapped[int] = mapped_column(Integer)
    start_event: Mapped[str] = mapped_column(String(40), default="CIP_RECEIVED")
    pausable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- Zone-2 member (PHI encrypted)
class Member(Base):
    """CIP-PA-274: highest-sensitivity fields stored only as pgcrypto ciphertext (bytea)."""

    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    member_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # pseudonymous
    member_name_enc: Mapped[bytes] = mapped_column(LargeBinary)
    member_dob_enc: Mapped[bytes] = mapped_column(LargeBinary)
    member_addr_enc: Mapped[bytes] = mapped_column(LargeBinary)
    member_id_enc: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- Master transaction (DCN)
class Transaction(Base):
    """CIP-PA-005: durable master transaction record, created at the earliest intake point."""

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    dcn: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    request_type: Mapped[str] = mapped_column(String(40), index=True)
    service_category: Mapped[str] = mapped_column(String(40))
    priority: Mapped[str] = mapped_column(String(20))
    status: Mapped[TxnStatus] = mapped_column(Enum(TxnStatus, name="txn_status"), default=TxnStatus.RECEIVED, index=True)
    provider_npi: Mapped[str] = mapped_column(String(10))
    answers: Mapped[dict] = mapped_column(JSONB, default=dict)  # non-PHI answers only
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id"))

    # Immutable timestamps (guarded by DB trigger)
    source_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cip_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sla_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    sla_policy_id: Mapped[int] = mapped_column(ForeignKey("sla_policies.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    member: Mapped[Member] = relationship()
    sla_policy: Mapped[SlaPolicy] = relationship()
    documents: Mapped[list[Document]] = relationship(back_populates="transaction", order_by="Document.created_at")
    sla_events: Mapped[list[SlaEvent]] = relationship(order_by="SlaEvent.occurred_at")


class SlaEvent(Base):
    """Append-only SLA lifecycle ledger (START / PAUSE / RESUME / COMPLETE)."""

    __tablename__ = "sla_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(20))
    policy_code: Mapped[str] = mapped_column(String(40))
    policy_version: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- Documents & lineage
class Document(Base):
    """CIP-PA-003: SHA-256 stored on the DCN record AND in blob metadata; lineage via parent."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    transaction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transactions.id"), index=True)
    parent_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    artifact_kind: Mapped[ArtifactKind] = mapped_column(Enum(ArtifactKind, name="artifact_kind"))
    requirement_code: Mapped[str] = mapped_column(String(40))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    client_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blob_path: Mapped[str] = mapped_column(String(500))
    integrity_state: Mapped[IntegrityState] = mapped_column(
        Enum(IntegrityState, name="integrity_state"), default=IntegrityState.VERIFIED
    )
    doc_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extracted: Mapped[dict] = mapped_column(JSONB, default=dict)  # de-identified codes only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    transaction: Mapped[Transaction] = relationship(back_populates="documents")
    integrity_checks: Mapped[list[IntegrityCheck]] = relationship(order_by="IntegrityCheck.checked_at")


class IntegrityCheck(Base):
    """CIP-PA-014: append-only record of every downstream hash re-verification."""

    __tablename__ = "integrity_checks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    expected_sha256: Mapped[str] = mapped_column(String(64))
    actual_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blob_meta_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- Transactional outbox
class OutboxEvent(Base):
    """CIP-PA-064/065: events written in the same DB txn as business data."""

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    aggregate_type: Mapped[str] = mapped_column(String(40))
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    topic: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[OutboxStatus] = mapped_column(Enum(OutboxStatus, name="outbox_status"), default=OutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------- Postgres log broker (Kafka-compatible semantics)
class BrokerMessage(Base):
    """A topic partition log. `offset` is monotonically increasing, like a Kafka offset."""

    __tablename__ = "broker_log"

    offset: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(80), index=True)
    key: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConsumerOffset(Base):
    """CIP-PA-289: manual offset commit per consumer group."""

    __tablename__ = "consumer_offsets"

    consumer_group: Mapped[str] = mapped_column(String(80), primary_key=True)
    topic: Mapped[str] = mapped_column(String(80), primary_key=True)
    committed_offset: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProcessedEvent(Base):
    """CIP-PA-289: idempotency ledger — (event_id, consumer_group) processed at most once."""

    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    consumer_group: Mapped[str] = mapped_column(String(80), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    outcome: Mapped[str] = mapped_column(String(20), default="OK")


class DuplicateDelivery(Base):
    __tablename__ = "duplicate_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64))
    consumer_group: Mapped[str] = mapped_column(String(80))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- Audit
class AccessAudit(Base):
    """Governed access to decrypted Zone-2 fields is always audited."""

    __tablename__ = "access_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_role: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(40))
    resource: Mapped[str] = mapped_column(String(100))
    allowed: Mapped[bool] = mapped_column(Boolean)
    purpose: Mapped[str | None] = mapped_column(String(200), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiAudit(Base):
    """CIP-PA-314/315: what was (and was not) allowed to reach the AI service."""

    __tablename__ = "ai_audit"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    capability: Mapped[str] = mapped_column(String(60))
    transaction_dcn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    context_sent: Mapped[dict] = mapped_column(JSONB)
    fields_blocked: Mapped[list] = mapped_column(JSONB, default=list)
    injection_flags: Mapped[list] = mapped_column(JSONB, default=list)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataQualityRun(Base):
    """CIP-PA-658: persisted scores per rule so trends are measurable."""

    __tablename__ = "dq_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    rule_code: Mapped[str] = mapped_column(String(40))
    dimension: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(200))
    population: Mapped[int] = mapped_column(Integer)
    failed: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float] = mapped_column(Float)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StagedUpload(Base):
    """Smart intake staging: files uploaded for reading before a DCN exists.

    Holds only metadata + the blob pointer; extracted text / PHI is never persisted here.
    Rows are removed when the intake is submitted (or purged after 24h).
    """

    __tablename__ = "staged_uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    blob_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
