"""End-to-end against a real PostgreSQL: ingest → outbox → consumers → integrity → decision."""
import hashlib
import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import engine
from app.workers import drain

NOTE = b"PROGRESS NOTE. History of present illness. Assessment: M54.5. Plan: MRI. CPT: 72148\n"
ORDER = b"RADIOLOGY ORDER - MRI lumbar spine. CPT: 72148. Dx M54.5\n"

ANSWERS = {
    "request_type": "PRIOR_AUTH", "service_category": "IMAGING", "is_urgent": False,
    "imaging_modality": "MRI", "conservative_therapy_weeks": 8, "has_clinical_notes": True,
    "member_name": "Test Person", "member_dob": "1980-05-17", "member_id": "W123456789",
    "member_address": "12 Oak Street, Austin, TX 78701", "provider_npi": "1234567893",
    "icd10_codes": "M54.5", "cpt_codes": "72148",
}


def unique_files(tag: str):
    return [("doc__clinical_notes", (f"note-{tag}.txt", NOTE + tag.encode(), "text/plain")),
            ("doc__imaging_order", (f"order-{tag}.txt", ORDER + tag.encode(), "text/plain"))]


def submit(client, answers=ANSWERS, files=None, manifest=None):
    files = files or [("doc__clinical_notes", ("note.txt", NOTE, "text/plain")),
                      ("doc__imaging_order", ("order.txt", ORDER, "text/plain"))]
    manifest = manifest if manifest is not None else [
        {"field": f, "filename": n, "sha256": hashlib.sha256(b).hexdigest()} for f, (n, b, _) in files]
    return client.post("/api/ingestion/submit", data={"answers": json.dumps(answers), "manifest": json.dumps(manifest)},
                       files=files)


@pytest.fixture(scope="module")
def txn(client):
    r = submit(client)
    assert r.status_code == 201, r.text
    drain()
    return client.get(f"/api/transactions/{r.json()['id']}").json()


def test_ingestion_creates_dcn_hash_and_encrypts_phi(txn):
    assert txn["dcn"].startswith("DCN-")
    note = next(d for d in txn["documents"] if d["filename"] == "note.txt")
    assert note["sha256"] == hashlib.sha256(NOTE).hexdigest()
    assert "member_name" not in txn["answers"] and "member_dob" not in txn["answers"]
    with engine.connect() as c:
        raw = c.execute(text("SELECT member_dob_enc FROM members LIMIT 1")).scalar_one()
    assert b"1980-05-17" not in bytes(raw)


def test_pipeline_classifies_extracts_and_builds_lineage(txn):
    assert txn["status"] == "IN_REVIEW"
    src = next(d for d in txn["documents"] if d["filename"] == "note.txt")
    assert src["doc_class"] == "CLINICAL_NOTE"
    assert "M54.5" in src["extracted"]["icd10_codes"] and "72148" in src["extracted"]["cpt_codes"]
    stages = [c["stage"] for c in src["integrity_checks"]]
    assert stages[:3] == ["INGESTION", "CLASSIFY", "EXTRACT"]
    derived = [d for d in txn["documents"] if d["parent_document_id"] == src["id"]]
    assert derived and derived[0]["artifact_kind"] == "DERIVATIVE_TEXT"


def test_no_phi_in_any_published_event(txn):
    with engine.connect() as c:
        payloads = c.execute(text("SELECT payload::text FROM broker_log")).scalars().all()
    blob = " ".join(payloads)
    for secret in ("Test Person", "1980-05-17", "W123456789", "Oak Street"):
        assert secret not in blob


def test_intake_timestamps_are_immutable(txn):
    with pytest.raises(DBAPIError):
        with engine.begin() as c:
            c.execute(text("UPDATE transactions SET cip_received_at = now() WHERE dcn = :d"), {"d": txn["dcn"]})
    with pytest.raises(DBAPIError):
        with engine.begin() as c:
            c.execute(text("DELETE FROM sla_events"))


def test_client_hash_mismatch_is_rejected(client):
    files = [("doc__clinical_notes", ("note.txt", NOTE, "text/plain")),
             ("doc__imaging_order", ("order.txt", ORDER, "text/plain"))]
    bad = [{"field": "doc__clinical_notes", "filename": "note.txt", "sha256": "0" * 64}]
    r = submit(client, files=files, manifest=bad)
    assert r.status_code == 409


def test_missing_conditional_document_is_rejected(client):
    r = submit(client, files=[("doc__clinical_notes", ("note.txt", NOTE, "text/plain"))])
    assert r.status_code == 422 and "doc:imaging_order" in r.json()["detail"]["errors"]


def test_idempotent_replay_skips_duplicate(client, txn):
    log = client.get("/api/events/log", params={"topic": "cip.documents.ingested"}).json()["messages"]
    r = client.post(f"/api/events/{log[0]['offset']}/replay").json()
    assert r["consumer_result"]["DUPLICATE_SKIPPED"] == 1 and r["consumer_result"]["PROCESSED"] == 0


def test_tampering_blocks_decision_and_quarantines(client):
    t = submit(client, files=unique_files("tamper")).json()
    drain()
    doc = next(d for d in t["documents"] if d["filename"] == "note-tamper.txt")
    client.post(f"/api/documents/{doc['id']}/simulate-tamper")
    r = client.post(f"/api/transactions/{t['id']}/decision", json={"decision": "APPROVED"})
    assert r.status_code == 423
    detail = client.get(f"/api/transactions/{t['id']}").json()
    assert detail["status"] == "QUARANTINED"


def test_decision_completes_sla(client):
    t = submit(client, answers={**ANSWERS, "member_id": "W987654321"}, files=unique_files("decide")).json()
    drain()
    r = client.post(f"/api/transactions/{t['id']}/decision", json={"decision": "APPROVED"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "COMPLETED" and body["sla"]["state"] == "MET"
    assert [e["event_type"] for e in body["sla_events"]] == ["START", "COMPLETE"]


def test_data_quality_run(client):
    dq = client.post("/api/data-quality/run").json()
    assert 0 <= dq["overall_score"] <= 100 and len(dq["rules"]) >= 10
