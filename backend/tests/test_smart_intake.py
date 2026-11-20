"""Upload Documents workflow: read PDF/Word, pre-fill, submit through the shared ingestion path."""
import hashlib
import io
import json

import pytest

from app.services import document_reader
from app.workers import drain
from tests.sample_docs import DENIAL_PARAS, REFERRAL_LINES, make_docx, make_pdf

PDF = make_pdf(REFERRAL_LINES)
DOCX = make_docx(REFERRAL_LINES)
DENIAL = make_docx(DENIAL_PARAS)


def test_docx_text_and_field_extraction():
    text = document_reader.docx_text(io.BytesIO(DOCX))
    assert "Meera Krishnan" in text
    f = document_reader.extract_fields(text, "x.docx")
    assert f["member_name"].value == "Meera Krishnan"
    assert f["member_dob"].value == "1985-02-14"          # 14/02/1985 parsed day-first
    assert f["member_id"].value == "W318840027"
    assert f["provider_npi"].value == "1234567893"
    assert f["service_category"].value == "IMAGING" and f["imaging_modality"].value == "MRI"
    assert f["conservative_therapy_weeks"].value == 8
    assert f["is_urgent"].value is True
    assert "M54.17" in f["icd10_codes"].value and f["cpt_codes"].value == "72148"


def test_pdf_reading_matches_docx():
    pdf_text = document_reader.read_text(PDF, "application/pdf")
    assert document_reader.extract_fields(pdf_text, "x.pdf")["member_id"].value == "W318840027"


@pytest.mark.parametrize("sentence, weeks", [
    ("Failed 8 weeks of physiotherapy and NSAIDs.", 8),            # original sample wording
    ("Completed physical therapy for 6 weeks without relief.", 6),
    ("PT x 6 wks, no improvement.", 6),
    ("She tried six weeks of conservative management.", 6),
    ("6-week course of physical therapy completed.", 6),
    ("Conservative care for the past 3 months.", 12),
    ("Weeks of conservative therapy: 10", 10),
    ("Conservative therapy (weeks): 7", 7),
    ("Despite 9 weeks of NSAIDs and home exercises, pain persists.", 9),
])
def test_conservative_therapy_weeks_phrasings(sentence, weeks):
    f = document_reader.extract_fields(f"Plan: MRI lumbar spine.\n{sentence}", "x")
    assert f["conservative_therapy_weeks"].value == weeks


def test_unrelated_week_mentions_are_not_therapy():
    f = document_reader.extract_fields("Symptoms started 2 weeks ago. Plan: MRI lumbar spine.", "x")
    assert "conservative_therapy_weeks" not in f


@pytest.mark.parametrize("sentence, code", [
    ("The request was denied as not medically necessary.", "MEDICAL_NECESSITY"),
    ("Denied due to lack of medical necessity.", "MEDICAL_NECESSITY"),
    ("The service does not meet medical necessity criteria.", "MEDICAL_NECESSITY"),
    ("Criteria were not met for this imaging study.", "MEDICAL_NECESSITY"),
    ("Denied for insufficient documentation.", "INSUFFICIENT_DOCS"),
    ("Denied: incomplete clinical records were submitted.", "INSUFFICIENT_DOCS"),
    ("Supporting documentation was not provided.", "INSUFFICIENT_DOCS"),
    ("The provider is non-participating.", "OUT_OF_NETWORK"),
    ("Service rendered out of network.", "OUT_OF_NETWORK"),
    ("This procedure is considered investigational.", "EXPERIMENTAL"),
    # a labelled reason line wins over other wording elsewhere in the letter
    ("We reviewed medical necessity guidelines.\nReason for denial: missing documentation", "INSUFFICIENT_DOCS"),
])
def test_denial_reason_phrasings(sentence, code):
    f = document_reader.extract_fields(f"NOTICE OF ADVERSE DETERMINATION\n{sentence}", "x")
    assert f["denial_reason"].value == code


def test_invalid_npi_is_not_prefilled():
    f = document_reader.extract_fields("Requesting Provider NPI: 1234567890", "x")
    assert "provider_npi" not in f


def _analyze(client, files):
    manifest = [{"filename": n, "sha256": hashlib.sha256(b).hexdigest()} for n, b, _ in files]
    return client.post("/api/smart-intake/analyze", data={"manifest": json.dumps(manifest)},
                       files=[("files", f) for f in files])


def test_analyze_rejects_legacy_doc_and_fake_docx(client):
    r = _analyze(client, [("old.doc", b"\xd0\xcf\x11\xe0junk", "application/msword"),
                          ("fake.docx", b"not a zip", "application/octet-stream")])
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert "save it as .docx" in errs["old.doc"] and "fake.docx" in errs


def test_analyze_then_submit_end_to_end(client):
    r = _analyze(client, [("referral.docx", DOCX, document_reader.DOCX_MIME),
                          ("denial.docx", DENIAL, document_reader.DOCX_MIME)])
    assert r.status_code == 200, r.text
    a = r.json()
    ans = a["answers"]
    assert ans["request_type"] == "APPEAL" and ans["original_auth_number"] == "PA-771204"
    assert ans["denial_reason"] == "MEDICAL_NECESSITY" and ans["member_name"] == "Meera Krishnan"
    slots = {f["filename"]: f["suggested_slot"] for f in a["files"]}
    assert slots["denial.docx"] == "denial_letter"
    assert a["sla_policy"]["code"] == "APPEAL:EXPEDITED"

    # user completes the remaining required documents by re-using the referral as the order + attestation
    ref = next(f for f in a["files"] if f["filename"] == "referral.docx")["file_id"]
    den = next(f for f in a["files"] if f["filename"] == "denial.docx")["file_id"]
    assignments = [{"file_id": ref, "requirement_code": "clinical_notes"},
                   {"file_id": den, "requirement_code": "denial_letter"},
                   {"file_id": ref, "requirement_code": "imaging_order"},
                   {"file_id": ref, "requirement_code": "physician_attestation"}]
    s = client.post("/api/smart-intake/submit", json={"batch_id": a["batch_id"], "answers": ans, "assignments": assignments})
    assert s.status_code == 201, s.text
    txn = s.json()
    assert txn["dcn"].startswith("DCN-") and txn["priority"] == "EXPEDITED"
    docx = [d for d in txn["documents"] if d["filename"] == "referral.docx"]
    assert docx[0]["content_type"] == document_reader.DOCX_MIME and docx[0]["sha256"] == hashlib.sha256(DOCX).hexdigest()

    drain()
    detail = client.get(f"/api/transactions/{txn['id']}").json()
    assert detail["status"] == "IN_REVIEW"
    src = next(d for d in detail["documents"] if d["filename"] == "referral.docx" and not d["parent_document_id"])
    assert "M54.17" in src["extracted"]["icd10_codes"]  # Word text flowed through classify/extract too

    # staging batch is consumed
    again = client.post("/api/smart-intake/submit", json={"batch_id": a["batch_id"], "answers": ans, "assignments": assignments})
    assert again.status_code == 404


def test_classic_wizard_still_rejects_docx(client):
    """Existing workflow unchanged: the original ingestion endpoint does not accept Word files."""
    answers = {"request_type": "PRIOR_AUTH", "service_category": "DME", "dme_acquisition": "RENTAL",
               "is_urgent": False, "has_clinical_notes": True, "member_name": "A B", "member_dob": "1980-01-01",
               "member_id": "W111111111", "member_address": "1 Road, City", "provider_npi": "1234567893",
               "icd10_codes": "G47.33"}
    r = client.post("/api/ingestion/submit", data={"answers": json.dumps(answers), "manifest": "[]"},
                    files=[("doc__clinical_notes", ("note.docx", DOCX, document_reader.DOCX_MIME))])
    assert r.status_code == 415


from tests.sample_docs import SCENARIOS  # noqa: E402

EXPECTED = {
    "case1_prior_auth_knee_arthroscopy.pdf": ("PRIOR_AUTH", "OUTPATIENT_SURGERY", False),
    "case2_concurrent_review_pneumonia.pdf": ("CONCURRENT_REVIEW", "INPATIENT", False),
    "case3_retro_review_behavioral_health.pdf": ("RETRO_REVIEW", "BEHAVIORAL_HEALTH", False),
    "case4_prior_auth_cpap_rental.pdf": ("PRIOR_AUTH", "DME", False),
    "case5_appeal_ct_head_urgent.pdf": ("APPEAL", "IMAGING", True),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_pdfs_prefill_a_complete_valid_intake(name):
    from app.services import questionnaire

    r = document_reader.analyse([("f", name, "application/pdf", make_pdf(SCENARIOS[name]))])
    a = r["answers"]
    rt, svc, urgent = EXPECTED[name]
    assert (a["request_type"], a["service_category"], a["is_urgent"]) == (rt, svc, urgent)
    _, errors = questionnaire.validate_answers(a)
    assert errors == {}, errors


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario_pdf_alone_covers_every_required_document(name):
    """A single combined letter (e.g. case 5: note + CT order + urgency attestation) must fill
    every required slot by default, so 'Imaging order is required' / 'Physician attestation of
    urgency is required' do not appear when the document already contains them."""
    from app.services import questionnaire

    r = document_reader.analyse([("f", name, "application/pdf", make_pdf(SCENARIOS[name]))])
    suggested = set(r["files"][0]["suggested_slots"])
    required = {d["code"] for d in questionnaire.required_documents(r["answers"]) if d["required"]}
    assert required <= suggested, f"missing {required - suggested}"
    applicable = {d["code"] for d in questionnaire.required_documents(r["answers"])}
    assert suggested <= applicable | {"other"}  # never suggests a slot that does not apply
