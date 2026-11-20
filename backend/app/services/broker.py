"""Message broker abstraction.

`PostgresLogBroker` (default) gives Kafka semantics with zero extra infrastructure:
append-only topic log with monotonically increasing offsets, consumer groups and
*manual* offset commits. `KafkaBroker` is used automatically when
`CIP_KAFKA_BOOTSTRAP_SERVERS` is set and `confluent-kafka` is installed.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import BrokerMessage, ConsumerOffset

log = logging.getLogger("cip.broker")


class Broker(Protocol):
    name: str

    def publish(self, topic: str, *, key: str, event_id: str, event_type: str,
                payload: dict[str, Any], db: Session | None = None) -> None: ...


class PostgresLogBroker:
    name = "postgres-log"

    def publish(self, topic, *, key, event_id, event_type, payload, db=None):
        assert db is not None, "PostgresLogBroker publishes within the relay's DB session"
        db.add(BrokerMessage(topic=topic, key=key, event_id=event_id, event_type=event_type, payload=payload))
        db.flush()

    # ---------------- consumer API
    @staticmethod
    def poll(db: Session, group: str, topic: str, max_records: int = 25) -> list[BrokerMessage]:
        committed = db.scalar(select(ConsumerOffset.committed_offset)
                              .where(ConsumerOffset.consumer_group == group, ConsumerOffset.topic == topic)) or 0
        return list(db.scalars(select(BrokerMessage)
                               .where(BrokerMessage.topic == topic, BrokerMessage.offset > committed)
                               .order_by(BrokerMessage.offset).limit(max_records)))

    @staticmethod
    def commit(db: Session, group: str, topic: str, offset: int) -> None:
        """Manual commit — only called *after* the message has been processed."""
        stmt = insert(ConsumerOffset).values(consumer_group=group, topic=topic, committed_offset=offset)
        stmt = stmt.on_conflict_do_update(
            index_elements=["consumer_group", "topic"],
            set_={"committed_offset": func.greatest(ConsumerOffset.committed_offset, offset), "updated_at": func.now()},
        )
        db.execute(stmt)


class KafkaBroker:  # pragma: no cover - requires a running Kafka cluster
    name = "kafka"

    def __init__(self, bootstrap: str):
        from confluent_kafka import Producer

        self._producer = Producer({
            "bootstrap.servers": bootstrap,
            "enable.idempotence": True,  # broker-side de-dup of producer retries
            "acks": "all",               # at-least-once durability
        })

    def publish(self, topic, *, key, event_id, event_type, payload, db=None):
        errors: list[Exception] = []
        self._producer.produce(topic, key=key, value=json.dumps(payload).encode(),
                               headers={"event_id": event_id, "event_type": event_type},
                               on_delivery=lambda err, _m: err and errors.append(Exception(str(err))))
        self._producer.flush(10)
        if errors:
            raise errors[0]


_broker: Broker | None = None


def get_broker() -> Broker:
    global _broker
    if _broker is None:
        bootstrap = get_settings().kafka_bootstrap_servers
        if bootstrap:
            try:
                _broker = KafkaBroker(bootstrap)
            except ImportError:
                log.warning("confluent-kafka not installed; falling back to Postgres log broker")
        _broker = _broker or PostgresLogBroker()
    return _broker
