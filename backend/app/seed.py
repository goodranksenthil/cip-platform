"""Demo data: ~40 realistic (synthetic) intakes over the last 14 days, run through the real pipeline.

All names / IDs are fictitious. Seeding uses the same `ingestion.ingest` use-case as the API,
so the demo data exercises hashing, encryption, outbox, consumers and integrity checks.
"""
from __future__ import annotations

import io
import logging
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Document, Transaction, TxnStatus
from app.services import data_quality, integrity, outbox, sla
from app.services.ingestion import IncomingFile, ingest
from app.services.storage import get_store
from app.workers import drain

log = logging.getLogger("cip.seed")

FIRST = ["Ava", "Liam", "Maya", "Noah", "Priya", "Arjun", "Sofia", "Ethan", "Zara", "Kabir", "Mia", "Leo"]
LAST = ["Sharma", "Patel", "Nguyen", "Garcia", "Okafor", "Kim", "Rossi", "Iyer", "Brown", "Haddad"]
CITIES = ["Austin, TX 78701", "Columbus, OH 43004", "Phoenix, AZ 85001", "Raleigh, NC 27601", "Denver, CO 80202"]
SCENARIOS = [
    # request_type, service_category, icd, cpt, extra answers
    ("PRIOR_AUTH", "IMAGING", "M54.5, M51.26", "72148", {"imaging_modality": "MRI", "conservative_therapy_weeks": 8}),
    ("PRIOR_AUTH", "OUTPATIENT_SURGERY", "M23.205, S83.241A", "29881", {}),
    ("PRIOR_AUTH", "INPATIENT", "I21.4, I25.10", "", {"admission_date": "2026-11-01", "length_of_stay": 4}),
    ("APPEAL", "IMAGING", "G43.909", "70553", {"imaging_modality": "CT", "original_auth_number": "PA-482913",
                                                 "denial_reason": "MEDICAL_NECESSITY"}),
    ("CONCURRENT_REVIEW", "INPATIENT", "J18.9, E11.9", "", {"admission_date": "2026-11-10", "length_of_stay": 6}),
    ("PRIOR_AUTH", "DME", "G47.33", "", {"dme_acquisition": "RENTAL"}),
    ("RETRO_REVIEW", "BEHAVIORAL_HEALTH", "F33.1", "", {"date_of_service": "2026-10-02"}),
    ("APPEAL", "OUTPATIENT_SURGERY", "K40.90", "49505", {"original_auth_number": "PA-551208", "denial_reason": "INSUFFICIENT_DOCS"}),
]

NOTE = """PROGRESS NOTE — {cls}
History of present illness: patient reports symptoms consistent with {icd}. Exam performed.
Assessment: {icd}. Plan: proceed with requested service. CPT: {cpt}
Subjective: improving with therapy. Reference id {nonce}.
"""
DENIAL = """NOTICE OF ADVERSE DETERMINATION
Your request was denied as not medically necessary. You have appeal rights. Denial ref {nonce}. Diagnosis {icd}.
"""
ORDER = """RADIOLOGY ORDER — imaging requested: MRI lumbar spine without contrast. CPT: {cpt}. Dx {icd}. Order {nonce}.
"""
ATTEST = """PHYSICIAN ATTESTATION: I certify that applying the standard timeframe could seriously jeopardize the
member's health. Urgent review requested. Signature on file. Ref {nonce}. Dx {icd}.
"""


def npi_with_check(prefix9: str) -> str:
    digits = [int(d) for d in "80840" + prefix9]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return prefix9 + str((10 - total % 10) % 10)


def _file(code: str, name: str, body: str) -> IncomingFile:
    return IncomingFile(code, name, io.BytesIO(body.encode()))


def seed_if_empty(n: int = 40) -> None:
    with SessionLocal() as db:
        sla.ensure_policies(db)
        if db.scalar(select(func.count()).select_from(Transaction)):
            return
    log.info("Seeding %s demo transactions…", n)
    rng = random.Random(42)
    now = datetime.now(timezone.utc)
    created: list[str] = []
    for i in range(n):
        rt, sc, icd, cpt, extra = SCENARIOS[i % len(SCENARIOS)]
        urgent = rng.random() < 0.3
        days_ago = rng.choices(range(14), weights=[3, 3, 3, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1])[0]
        src = now - timedelta(days=days_ago, hours=rng.randint(0, 20), minutes=rng.randint(0, 59))
        answers = {
            "request_type": rt, "service_category": sc, "is_urgent": urgent, "has_clinical_notes": True,
            "member_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "member_dob": f"{rng.randint(1950, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "member_id": f"W{rng.randint(10**8, 10**9 - 1):09d}",
            "member_address": f"{rng.randint(10, 9999)} Oak Street, {rng.choice(CITIES)}",
            "provider_npi": npi_with_check(f"1{rng.randint(10**7, 10**8 - 1):08d}"),
            "icd10_codes": icd, "cpt_codes": cpt, **extra,
        }
        if urgent:
            answers["urgency_justification"] = "Progressive neurological deficit; delay risks permanent loss of function."
        ctx = {"icd": icd, "cpt": cpt or "n/a", "nonce": f"{i:04d}-{rng.randint(1000, 9999)}", "cls": sc}
        files = [_file("clinical_notes", f"progress_note_{i:03d}.txt", NOTE.format(**ctx))]
        if rt == "APPEAL":
            files.append(_file("denial_letter", f"denial_{i:03d}.txt", DENIAL.format(**ctx)))
        if sc == "IMAGING":
            files.append(_file("imaging_order", f"order_{i:03d}.txt", ORDER.format(**ctx)))
        if urgent:
            files.append(_file("physician_attestation", f"attestation_{i:03d}.txt", ATTEST.format(**ctx)))
        with SessionLocal() as db:
            txn = ingest(db, answers, files, source_received_at=src)
            created.append(str(txn.id))

    drain(max_rounds=40)

    # Give the demo a realistic spread of lifecycle states
    with SessionLocal() as db:
        txns = db.scalars(select(Transaction).order_by(Transaction.source_received_at)).all()
        for idx, txn in enumerate(txns):
            if txn.status != TxnStatus.IN_REVIEW:
                continue
            age_h = (now - txn.source_received_at).total_seconds() / 3600
            target = txn.sla_policy.target_hours
            if (idx % 3 == 0 and age_h > 24) or (age_h > target * 0.8 and idx % 5 != 0):
                done = txn.source_received_at + timedelta(hours=min(age_h - 1, rng.uniform(6, target * 1.05)))
                sla.record_event(db, txn, "COMPLETE", at=done, reason="Determination issued")
                txn.status, txn.completed_at = TxnStatus.COMPLETED, done
                outbox.enqueue(db, "ReviewDecided", "transaction", str(txn.id), dcn=txn.dcn,
                               transaction_id=str(txn.id), decision=rng.choice(["APPROVED", "APPROVED", "DENIED"]))
            elif idx % 7 == 1 and txn.sla_policy.pausable:
                sla.record_event(db, txn, "PAUSE", at=max(txn.source_received_at + timedelta(hours=1),
                                                         now - timedelta(hours=rng.randint(2, 30))),
                                 reason="Awaiting additional information from provider")
        db.commit()

        # One tampered document to demonstrate quarantine (CIP-PA-014)
        victim = db.scalars(select(Document).join(Transaction)
                            .where(Document.parent_document_id.is_(None), Transaction.status == TxnStatus.IN_REVIEW)
                            .order_by(Document.created_at.desc())).first()
        if victim:
            get_store().append(victim.blob_path, b"\n[edited after ingestion]")
            integrity.verify(db, victim, "CLINICAL_REVIEW", raise_on_fail=False)
            db.commit()

    drain(max_rounds=10)
    with SessionLocal() as db:
        data_quality.run(db)
    log.info("Seed complete.")
