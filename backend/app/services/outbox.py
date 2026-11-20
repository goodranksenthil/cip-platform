"""CIP-PA-064 / 065 — Transactional outbox + relay.

`enqueue()` is called *inside* the business transaction, so the event row commits or rolls
back atomically with the data it describes (no dual-write problem).

`relay_once()` is the OutboxRelay background process:
    SELECT ... FOR UPDATE SKIP LOCKED
lets N relay replicas run concurrently without double-publishing or blocking each other.
Publishing is at-least-once: a crash after publish but before commit re-sends the event,
which is why consumers are idempotent (see consumers.py).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import OutboxEvent, OutboxStatus
from app.security import phi_guard
from app.services.broker import get_broker

log = logging.getLogger("cip.outbox")

TOPICS = {
    "DocumentIngested": "cip.documents.ingested",
    "DocumentClassified": "cip.documents.classified",
    "DocumentExtracted": "cip.documents.extracted",
    "IntegrityViolation": "cip.integrity.violations",
    "SlaStateChanged": "cip.sla.events",
    "ReviewDecided": "cip.review.decisions",
}


def enqueue(db: Session, event_type: str, aggregate_type: str, aggregate_id: str, **fields: Any) -> OutboxEvent:
    event_id = str(uuid.uuid4())
    payload = {"event_id": event_id, "occurred_at": datetime.now(timezone.utc).isoformat(), **fields}
    phi_guard.enforce(event_type, payload)  # CIP-PA-275: fail closed before it is even stored
    ev = OutboxEvent(id=uuid.UUID(event_id), aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                     topic=TOPICS[event_type], event_type=event_type, payload=payload)
    db.add(ev)
    return ev


def _backoff(attempt: int) -> timedelta:
    return timedelta(seconds=min(2 ** attempt, 300))  # exponential, capped at 5 min


def relay_once(db: Session, batch_size: int | None = None) -> dict[str, int]:
    settings = get_settings()
    batch_size = batch_size or settings.relay_batch_size
    broker = get_broker()
    now = datetime.now(timezone.utc)

    rows = db.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.status == OutboxStatus.PENDING, OutboxEvent.next_attempt_at <= now)
        .order_by(OutboxEvent.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    ).all()

    published = failed = dead = 0
    for ev in rows:
        try:
            broker.publish(ev.topic, key=ev.aggregate_id, event_id=str(ev.id),
                           event_type=ev.event_type, payload=ev.payload, db=db)
            ev.status, ev.published_at = OutboxStatus.PUBLISHED, datetime.now(timezone.utc)
            published += 1
        except Exception as exc:  # broker down, network blip, etc.
            ev.attempts += 1
            ev.last_error = str(exc)[:500]
            if ev.attempts >= settings.relay_max_attempts:
                ev.status = OutboxStatus.DEAD
                dead += 1
            else:
                ev.next_attempt_at = now + _backoff(ev.attempts)
                failed += 1
            log.warning("outbox publish failed id=%s attempt=%s err=%s", ev.id, ev.attempts, exc)
    db.commit()
    return {"claimed": len(rows), "published": published, "retrying": failed, "dead_lettered": dead}
