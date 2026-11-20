"""CIP-PA-289 — idempotent consumers with manual offset commit.

For each message:  dedupe-insert → handle → commit offset, all in ONE DB transaction.
* Redelivery after a crash re-runs the transaction; the `processed_events` primary key
  (event_id, consumer_group) guarantees the business effect happens at most once.
* Combined with at-least-once publishing this yields *effectively-once* outcomes.
* Poison messages are retried, then parked with outcome=FAILED so the partition keeps moving.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import BrokerMessage, Document, DuplicateDelivery, ProcessedEvent, Transaction, TxnStatus
from app.services import extraction, integrity, outbox
from app.services.broker import PostgresLogBroker

log = logging.getLogger("cip.consumers")

Handler = Callable[[Session, BrokerMessage], None]


# --------------------------------------------------------------------------- handlers
def handle_ingested(db: Session, msg: BrokerMessage) -> None:
    """Step 4 — Classify. Integrity is re-verified before the bytes are read."""
    txn = db.get(Transaction, uuid.UUID(msg.payload["transaction_id"]))
    if txn is None or txn.status == TxnStatus.QUARANTINED:
        return
    for doc_id in msg.payload["document_ids"]:
        doc = db.get(Document, uuid.UUID(doc_id))
        try:
            integrity.verify(db, doc, "CLASSIFY")
        except integrity.IntegrityFailure:
            return  # quarantined + IntegrityViolation event already queued; stop processing
        text = extraction.extract_text(doc)
        doc.doc_class, doc.classification_confidence = extraction.classify(text, doc.requirement_code)
        outbox.enqueue(db, "DocumentClassified", "document", str(doc.id), dcn=txn.dcn, transaction_id=str(txn.id),
                       document_id=str(doc.id), doc_class=doc.doc_class, confidence=doc.classification_confidence)
    txn.status = TxnStatus.CLASSIFIED


def handle_classified(db: Session, msg: BrokerMessage) -> None:
    """Step 5 — Extract. Verify again, create derivative with lineage, emit codes."""
    doc = db.get(Document, uuid.UUID(msg.payload["document_id"]))
    txn = doc.transaction
    if txn.status == TxnStatus.QUARANTINED:
        return
    try:
        integrity.verify(db, doc, "EXTRACT")
    except integrity.IntegrityFailure:
        return
    text = extraction.extract_text(doc)
    if text.strip():
        extraction.create_derivative(db, doc, text)
    doc.extracted = extraction.extract_codes(text)
    outbox.enqueue(db, "DocumentExtracted", "document", str(doc.id), dcn=txn.dcn, transaction_id=str(txn.id),
                   document_id=str(doc.id), **doc.extracted)
    db.flush()
    sources = [d for d in txn.documents if d.parent_document_id is None]
    if all(d.extracted for d in sources):
        txn.status = TxnStatus.IN_REVIEW  # every source artifact classified + extracted


def handle_noop(db: Session, msg: BrokerMessage) -> None:
    """Audit / SLA monitor sink — the dedupe ledger is the side effect."""


@dataclass(frozen=True)
class Subscription:
    group: str
    topic: str
    handler: Handler


SUBSCRIPTIONS = [
    Subscription("classifier", "cip.documents.ingested", handle_ingested),
    Subscription("extractor", "cip.documents.classified", handle_classified),
    Subscription("sla-monitor", "cip.sla.events", handle_noop),
    Subscription("integrity-alerts", "cip.integrity.violations", handle_noop),
]


# --------------------------------------------------------------------------- engine
def _claim(db: Session, event_id: str, group: str) -> bool:
    """True if this (event, group) has never been processed. Atomic via PK + ON CONFLICT."""
    res = db.execute(insert(ProcessedEvent).values(event_id=event_id, consumer_group=group)
                     .on_conflict_do_nothing().returning(ProcessedEvent.event_id))
    return res.first() is not None


def process_message(sub: Subscription, msg_offset: int, max_retries: int = 3) -> str:
    for attempt in range(1, max_retries + 1):
        db = SessionLocal()
        try:
            msg = db.get(BrokerMessage, msg_offset)
            if not _claim(db, msg.event_id, sub.group):
                db.add(DuplicateDelivery(event_id=msg.event_id, consumer_group=sub.group))
                PostgresLogBroker.commit(db, sub.group, sub.topic, msg.offset)
                db.commit()
                return "DUPLICATE_SKIPPED"
            sub.handler(db, msg)
            PostgresLogBroker.commit(db, sub.group, sub.topic, msg.offset)  # manual commit AFTER work
            db.commit()
            return "PROCESSED"
        except Exception as exc:
            db.rollback()
            log.exception("consumer %s failed on offset %s (attempt %s): %s", sub.group, msg_offset, attempt, exc)
        finally:
            db.close()
    # park the poison message so the partition is not blocked
    with SessionLocal() as db:
        msg = db.get(BrokerMessage, msg_offset)
        db.execute(insert(ProcessedEvent).values(event_id=msg.event_id, consumer_group=sub.group, outcome="FAILED")
                   .on_conflict_do_nothing())
        PostgresLogBroker.commit(db, sub.group, sub.topic, msg_offset)
        db.commit()
    return "FAILED"


def consume_once(max_records: int = 25) -> dict[str, int]:
    stats = {"PROCESSED": 0, "DUPLICATE_SKIPPED": 0, "FAILED": 0}
    for sub in SUBSCRIPTIONS:
        with SessionLocal() as db:
            offsets = [m.offset for m in PostgresLogBroker.poll(db, sub.group, sub.topic, max_records)]
        for off in offsets:
            stats[process_message(sub, off)] += 1
    return stats


def consumer_lag(db: Session) -> list[dict]:
    from sqlalchemy import func

    from app.models import ConsumerOffset

    out = []
    for sub in SUBSCRIPTIONS:
        end = db.scalar(select(func.coalesce(func.max(BrokerMessage.offset), 0)).where(BrokerMessage.topic == sub.topic))
        committed = db.scalar(select(ConsumerOffset.committed_offset).where(
            ConsumerOffset.consumer_group == sub.group, ConsumerOffset.topic == sub.topic)) or 0
        total = db.scalar(select(func.count()).select_from(BrokerMessage).where(BrokerMessage.topic == sub.topic))
        pending = db.scalar(select(func.count()).select_from(BrokerMessage).where(
            BrokerMessage.topic == sub.topic, BrokerMessage.offset > committed))
        out.append({"group": sub.group, "topic": sub.topic, "log_end_offset": end, "committed_offset": committed,
                    "lag": pending, "messages": total})
    return out
