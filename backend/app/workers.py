"""Background workers: OutboxRelay + consumer groups, running in a daemon thread.

In production each would be its own deployment (e.g. `python -m app.workers relay`), scaled
horizontally — safe because of SKIP LOCKED and the idempotency ledger.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.services import consumers, outbox

log = logging.getLogger("cip.workers")


def drain(max_rounds: int = 20) -> dict[str, int]:
    """Run relay + consumers until nothing is left (used by seed, tests and the UI 'Run now')."""
    totals = {"published": 0, "processed": 0, "duplicates": 0, "failed": 0, "rounds": 0}
    for _ in range(max_rounds):
        with SessionLocal() as db:
            r = outbox.relay_once(db)
        c = consumers.consume_once()
        totals["published"] += r["published"]
        totals["processed"] += c["PROCESSED"]
        totals["duplicates"] += c["DUPLICATE_SKIPPED"]
        totals["failed"] += c["FAILED"]
        totals["rounds"] += 1
        if r["claimed"] == 0 and sum(c.values()) == 0:
            break
    return totals


class WorkerThread(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="cip-workers", daemon=True)
        self._halt = threading.Event()

    def run(self) -> None:
        interval = get_settings().relay_interval_seconds
        while not self._halt.is_set():
            try:
                drain(max_rounds=3)
            except Exception:  # keep the loop alive; errors are logged
                log.exception("worker loop error")
            self._halt.wait(interval)

    def stop(self) -> None:
        self._halt.set()


if __name__ == "__main__":  # python -m app.workers
    logging.basicConfig(level=logging.INFO)
    t = WorkerThread()
    t.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        t.stop()
        sys.exit(0)
