"""CIP-PA-658 — measurable, risk-based data-quality rules.

Each rule is SQL returning (population, failed). Scores are persisted per run so trends
and threshold breaches are measurable over time. Thresholds are risk-weighted: integrity
and traceability rules on clinical artifacts must be 100%.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import DataQualityRun


@dataclass(frozen=True)
class Rule:
    code: str
    dimension: str
    description: str
    sql: str  # must return population, failed
    threshold: float
    risk: str


RULES: list[Rule] = [
    Rule("DQ-COMP-01", "Completeness", "Every transaction has at least one source document",
         """SELECT count(*), count(*) FILTER (WHERE NOT EXISTS (
              SELECT 1 FROM documents d WHERE d.transaction_id = t.id AND d.parent_document_id IS NULL))
            FROM transactions t""", 100, "HIGH"),
    Rule("DQ-COMP-02", "Completeness", "Transactions have diagnosis codes captured",
         """SELECT count(*), count(*) FILTER (WHERE coalesce(answers->>'icd10_codes','') = '') FROM transactions""",
         98, "MEDIUM"),
    Rule("DQ-VAL-01", "Validity", "Document fingerprints are well-formed SHA-256 (64 hex)",
         """SELECT count(*), count(*) FILTER (WHERE sha256 !~ '^[0-9a-f]{64}$') FROM documents""", 100, "HIGH"),
    Rule("DQ-VAL-02", "Validity", "Provider NPI is 10 digits",
         """SELECT count(*), count(*) FILTER (WHERE provider_npi !~ '^[0-9]{10}$') FROM transactions""", 100, "MEDIUM"),
    Rule("DQ-CONS-01", "Consistency", "Client-side and server-side hashes agree where both exist",
         """SELECT count(*) FILTER (WHERE client_sha256 IS NOT NULL),
                   count(*) FILTER (WHERE client_sha256 IS NOT NULL AND client_sha256 <> sha256) FROM documents""",
         100, "HIGH"),
    Rule("DQ-CONS-02", "Consistency", "Completed transactions have an SLA COMPLETE event",
         """SELECT count(*), count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM sla_events e
              WHERE e.transaction_id = t.id AND e.event_type='COMPLETE'))
            FROM transactions t WHERE t.status = 'COMPLETED'""", 100, "MEDIUM"),
    Rule("DQ-TIME-01", "Timeliness", "Open transactions processed past RECEIVED within 15 minutes",
         """SELECT count(*), count(*) FILTER (WHERE status='RECEIVED' AND cip_received_at < now() - interval '15 minutes')
            FROM transactions""", 95, "MEDIUM"),
    Rule("DQ-TIME-02", "Timeliness", "Outbox events published within 60 seconds",
         """SELECT count(*), count(*) FILTER (WHERE (status='PENDING' AND created_at < now() - interval '60 seconds')
              OR status='DEAD') FROM outbox_events""", 99, "HIGH"),
    Rule("DQ-INT-01", "Integrity", "All downstream integrity verifications passed",
         """SELECT count(*), count(*) FILTER (WHERE NOT passed) FROM integrity_checks""", 100, "HIGH"),
    Rule("DQ-TRACE-01", "Traceability", "Derivative artifacts link to an existing source artifact",
         """SELECT count(*), count(*) FILTER (WHERE p.id IS NULL) FROM documents d
            LEFT JOIN documents p ON p.id = d.parent_document_id WHERE d.artifact_kind='DERIVATIVE_TEXT'""",
         100, "HIGH"),
    Rule("DQ-ACC-01", "Accuracy", "Classification confidence ≥ 0.40 on classified documents",
         """SELECT count(*) FILTER (WHERE doc_class IS NOT NULL),
                   count(*) FILTER (WHERE doc_class IS NOT NULL AND coalesce(classification_confidence,0) < 0.40)
            FROM documents WHERE parent_document_id IS NULL""", 90, "LOW"),
]


def run(db: Session) -> uuid.UUID:
    run_id = uuid.uuid4()
    for r in RULES:
        population, failed = db.execute(text(r.sql)).one()
        population, failed = int(population or 0), int(failed or 0)
        score = 100.0 if population == 0 else round(100 * (population - failed) / population, 2)
        db.add(DataQualityRun(run_id=run_id, rule_code=r.code, dimension=r.dimension, description=r.description,
                              population=population, failed=failed, score=score, threshold=r.threshold))
    db.commit()
    return run_id


def latest(db: Session) -> dict:
    last = db.execute(select(DataQualityRun.run_id, func.max(DataQualityRun.run_at))
                      .group_by(DataQualityRun.run_id).order_by(func.max(DataQualityRun.run_at).desc()).limit(1)).first()
    if not last:
        run(db)
        return latest(db)
    rows = db.scalars(select(DataQualityRun).where(DataQualityRun.run_id == last[0]).order_by(DataQualityRun.rule_code)).all()
    risk = {r.code: r.risk for r in RULES}
    weights = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    dims: dict[str, list[float]] = {}
    for r in rows:
        dims.setdefault(r.dimension, []).append(r.score)
    wsum = sum(weights[risk[r.rule_code]] for r in rows)
    overall = round(sum(r.score * weights[risk[r.rule_code]] for r in rows) / wsum, 1) if wsum else 100.0
    return {
        "run_id": str(last[0]), "run_at": last[1].isoformat(), "overall_score": overall,
        "dimensions": [{"dimension": d, "score": round(sum(s) / len(s), 1)} for d, s in dims.items()],
        "rules": [{"code": r.rule_code, "dimension": r.dimension, "description": r.description,
                   "population": r.population, "failed": r.failed, "score": r.score, "threshold": r.threshold,
                   "risk": risk[r.rule_code], "passing": r.score >= r.threshold} for r in rows],
    }


def history(db: Session, limit: int = 20) -> list[dict]:
    rows = db.execute(select(DataQualityRun.run_id, func.min(DataQualityRun.run_at), func.avg(DataQualityRun.score))
                      .group_by(DataQualityRun.run_id).order_by(func.min(DataQualityRun.run_at).desc()).limit(limit)).all()
    return [{"run_id": str(r[0]), "run_at": r[1].isoformat(), "avg_score": round(float(r[2]), 1)} for r in reversed(rows)]
