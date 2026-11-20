"""Declarative, condition-based intake questionnaire.

The *same* JSON definition is served to the React app (which renders it dynamically) and
evaluated here on the server. The client gives instant UX; the server is the authority —
hidden answers are stripped, required-ness is re-derived, and every validator re-runs.

Condition DSL (recursive):
    {"q": "<question_id>", "op": "eq|ne|in|not_in|truthy|falsy|gt|lt", "value": ...}
    {"all": [cond, ...]} | {"any": [cond, ...]} | {"not": cond}
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

Cond = dict[str, Any]

REQUEST_TYPES = [
    {"value": "PRIOR_AUTH", "label": "Prior Authorization"},
    {"value": "APPEAL", "label": "Appeal of Denial"},
    {"value": "RETRO_REVIEW", "label": "Retrospective Review"},
    {"value": "CONCURRENT_REVIEW", "label": "Concurrent Review"},
]
SERVICE_CATEGORIES = [
    {"value": "INPATIENT", "label": "Inpatient Admission"},
    {"value": "OUTPATIENT_SURGERY", "label": "Outpatient Surgery"},
    {"value": "IMAGING", "label": "Advanced Imaging"},
    {"value": "DME", "label": "Durable Medical Equipment"},
    {"value": "BEHAVIORAL_HEALTH", "label": "Behavioral Health"},
]

IS_APPEAL: Cond = {"q": "request_type", "op": "eq", "value": "APPEAL"}
IS_IMAGING: Cond = {"q": "service_category", "op": "eq", "value": "IMAGING"}
IS_INPATIENT: Cond = {"any": [{"q": "service_category", "op": "eq", "value": "INPATIENT"},
                              {"q": "request_type", "op": "eq", "value": "CONCURRENT_REVIEW"}]}
IS_URGENT: Cond = {"q": "is_urgent", "op": "truthy"}

DEFINITION: dict[str, Any] = {
    "id": "cip-intake",
    "version": 3,
    "sections": [
        {"id": "request", "title": "Request details", "description": "What is being requested and how urgent is it?"},
        {"id": "member", "title": "Member & provider", "description": "PHI fields are encrypted in Postgres (pgcrypto) and never published to events."},
        {"id": "clinical", "title": "Clinical documents", "description": "Upload exactly what this request type requires."},
    ],
    "questions": [
        # ---------------- Request
        {"id": "request_type", "section": "request", "type": "select", "label": "Request type",
         "options": REQUEST_TYPES, "required": True},
        {"id": "service_category", "section": "request", "type": "select", "label": "Service category",
         "options": SERVICE_CATEGORIES, "required": True},
        {"id": "is_urgent", "section": "request", "type": "boolean",
         "label": "Could a standard timeframe seriously jeopardise the member's life, health or ability to regain function?",
         "help": "Answering Yes selects the EXPEDITED SLA policy (72h) and requires a physician attestation."},
        {"id": "urgency_justification", "section": "request", "type": "textarea", "label": "Clinical justification for expedited review",
         "visible_when": IS_URGENT, "required_when": IS_URGENT,
         "validators": [{"type": "min_length", "value": 30, "message": "Provide at least 30 characters of clinical rationale"}]},
        {"id": "original_auth_number", "section": "request", "type": "text", "label": "Original authorization number",
         "placeholder": "PA-123456", "visible_when": IS_APPEAL, "required_when": IS_APPEAL,
         "validators": [{"type": "pattern", "value": r"^PA-\d{6}$", "message": "Format must be PA-######"}]},
        {"id": "denial_reason", "section": "request", "type": "select", "label": "Denial reason being appealed",
         "visible_when": IS_APPEAL, "required_when": IS_APPEAL,
         "options": [{"value": "MEDICAL_NECESSITY", "label": "Not medically necessary"},
                     {"value": "INSUFFICIENT_DOCS", "label": "Insufficient documentation"},
                     {"value": "OUT_OF_NETWORK", "label": "Out of network"},
                     {"value": "EXPERIMENTAL", "label": "Experimental / investigational"}]},
        {"id": "date_of_service", "section": "request", "type": "date", "label": "Date of service",
         "visible_when": {"q": "request_type", "op": "eq", "value": "RETRO_REVIEW"},
         "required_when": {"q": "request_type", "op": "eq", "value": "RETRO_REVIEW"},
         "validators": [{"type": "past_date", "message": "Retrospective reviews must reference a past date"}]},
        {"id": "admission_date", "section": "request", "type": "date", "label": "Admission date",
         "visible_when": IS_INPATIENT, "required_when": IS_INPATIENT},
        {"id": "length_of_stay", "section": "request", "type": "number", "label": "Requested length of stay (days)",
         "visible_when": IS_INPATIENT, "required_when": IS_INPATIENT,
         "validators": [{"type": "min", "value": 1}, {"type": "max", "value": 120}]},
        {"id": "imaging_modality", "section": "request", "type": "select", "label": "Imaging modality",
         "visible_when": IS_IMAGING, "required_when": IS_IMAGING,
         "options": [{"value": "MRI", "label": "MRI"}, {"value": "CT", "label": "CT"}, {"value": "PET", "label": "PET"}]},
        {"id": "conservative_therapy_weeks", "section": "request", "type": "number",
         "label": "Weeks of conservative therapy completed",
         "help": "MRI requests for musculoskeletal indications require ≥ 6 weeks of conservative therapy.",
         "visible_when": {"all": [IS_IMAGING, {"q": "imaging_modality", "op": "eq", "value": "MRI"}]},
         "required_when": {"all": [IS_IMAGING, {"q": "imaging_modality", "op": "eq", "value": "MRI"}]},
         "validators": [{"type": "min", "value": 6, "message": "Policy requires at least 6 weeks for MRI"}]},
        {"id": "dme_acquisition", "section": "request", "type": "select", "label": "Rental or purchase?",
         "visible_when": {"q": "service_category", "op": "eq", "value": "DME"},
         "required_when": {"q": "service_category", "op": "eq", "value": "DME"},
         "options": [{"value": "RENTAL", "label": "Rental"}, {"value": "PURCHASE", "label": "Purchase"}]},
        # ---------------- Member & provider (PHI)
        {"id": "member_name", "section": "member", "type": "text", "label": "Member full name", "phi": True, "required": True},
        {"id": "member_dob", "section": "member", "type": "date", "label": "Date of birth", "phi": True, "required": True,
         "validators": [{"type": "past_date"}]},
        {"id": "member_id", "section": "member", "type": "text", "label": "Member ID", "phi": True, "required": True,
         "placeholder": "W123456789",
         "validators": [{"type": "pattern", "value": r"^[A-Z]\d{9}$", "message": "1 letter followed by 9 digits"}]},
        {"id": "member_address", "section": "member", "type": "textarea", "label": "Member address", "phi": True, "required": True},
        {"id": "provider_npi", "section": "member", "type": "text", "label": "Requesting provider NPI", "required": True,
         "placeholder": "1234567893", "validators": [{"type": "npi"}]},
        {"id": "icd10_codes", "section": "member", "type": "text", "label": "Diagnosis codes (ICD-10)", "required": True,
         "placeholder": "M54.5, M51.26", "validators": [{"type": "icd10_list"}]},
        {"id": "cpt_codes", "section": "member", "type": "text", "label": "Procedure codes (CPT)",
         "placeholder": "72148", "required_when": {"q": "service_category", "op": "in", "value": ["OUTPATIENT_SURGERY", "IMAGING"]},
         "validators": [{"type": "cpt_list"}]},
        # ---------------- Clinical
        {"id": "has_clinical_notes", "section": "clinical", "type": "boolean",
         "label": "Are supporting clinical notes available?", "required": True},
    ],
    "documents": [
        {"code": "clinical_notes", "label": "Clinical notes", "description": "Progress notes, H&P, consult notes",
         "visible_when": {"q": "has_clinical_notes", "op": "truthy"},
         "required_when": {"q": "has_clinical_notes", "op": "truthy"}, "max_files": 5},
        {"code": "denial_letter", "label": "Original denial letter", "description": "Required for every appeal",
         "visible_when": IS_APPEAL, "required_when": IS_APPEAL, "max_files": 1},
        {"code": "imaging_order", "label": "Imaging order", "description": "Signed order from the ordering provider",
         "visible_when": IS_IMAGING, "required_when": IS_IMAGING, "max_files": 1},
        {"code": "physician_attestation", "label": "Physician attestation of urgency",
         "description": "Required for expedited review", "visible_when": IS_URGENT, "required_when": IS_URGENT, "max_files": 1},
        {"code": "discharge_summary", "label": "Discharge / census summary", "description": "Optional for inpatient",
         "visible_when": IS_INPATIENT, "max_files": 2},
        {"code": "other", "label": "Other supporting documents", "description": "Optional", "max_files": 5},
    ],
    "accept": {"mime": ["application/pdf", "image/png", "image/jpeg", "text/plain"],
               "extensions": [".pdf", ".png", ".jpg", ".jpeg", ".txt"]},
}

QUESTIONS = {q["id"]: q for q in DEFINITION["questions"]}
PHI_QUESTION_IDS = {q["id"] for q in DEFINITION["questions"] if q.get("phi")}


# --------------------------------------------------------------------------- evaluator
def _truthy(v: Any) -> bool:
    return v not in (None, "", False, [], "false", "no")


def evaluate(cond: Cond | None, answers: dict[str, Any]) -> bool:
    if cond is None:
        return True
    if "all" in cond:
        return all(evaluate(c, answers) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, answers) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], answers)
    v = answers.get(cond["q"])
    op, target = cond["op"], cond.get("value")
    match op:
        case "eq": return v == target
        case "ne": return v != target
        case "in": return v in target
        case "not_in": return v not in target
        case "truthy": return _truthy(v)
        case "falsy": return not _truthy(v)
        case "gt": return v is not None and float(v) > float(target)
        case "lt": return v is not None and float(v) < float(target)
    raise ValueError(f"Unknown operator {op}")


# --------------------------------------------------------------------------- validators
ICD10 = re.compile(r"^[A-TV-Z]\d[0-9A-Z](\.[0-9A-Z]{1,4})?$")
CPT = re.compile(r"^(\d{4}[0-9A-Z]|\d{5})$")


def npi_is_valid(npi: str) -> bool:
    """NPI check digit: Luhn over '80840' + first 9 digits (CMS spec)."""
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    digits = [int(d) for d in "80840" + npi[:9]]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return (10 - total % 10) % 10 == int(npi[9])


def split_codes(value: str) -> list[str]:
    return [c.strip().upper() for c in re.split(r"[,\s;]+", value or "") if c.strip()]


def run_validator(v: dict, value: Any) -> str | None:
    t = v["type"]
    msg = v.get("message")
    if t == "pattern" and not re.fullmatch(v["value"], str(value)):
        return msg or "Invalid format"
    if t == "min_length" and len(str(value)) < v["value"]:
        return msg or f"Minimum {v['value']} characters"
    if t in ("min", "max"):
        try:
            n = float(value)
        except (TypeError, ValueError):
            return "Must be a number"
        if t == "min" and n < v["value"]:
            return msg or f"Must be ≥ {v['value']}"
        if t == "max" and n > v["value"]:
            return msg or f"Must be ≤ {v['value']}"
    if t == "past_date":
        try:
            if date.fromisoformat(str(value)) >= date.today():
                return msg or "Date must be in the past"
        except ValueError:
            return "Invalid date"
    if t == "npi" and not npi_is_valid(str(value)):
        return msg or "Invalid NPI (check digit failed)"
    if t == "icd10_list":
        bad = [c for c in split_codes(value) if not ICD10.match(c)]
        if bad:
            return f"Invalid ICD-10: {', '.join(bad)}"
    if t == "cpt_list":
        bad = [c for c in split_codes(value) if not CPT.match(c)]
        if bad:
            return f"Invalid CPT: {', '.join(bad)}"
    return None


# --------------------------------------------------------------------------- public API
def validate_answers(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (clean_answers, errors). Hidden questions are stripped from the result."""
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for q in DEFINITION["questions"]:
        qid = q["id"]
        if not evaluate(q.get("visible_when"), raw):
            continue
        value = raw.get(qid)
        if isinstance(value, str):
            value = value.strip()
        required = q.get("required", False) or (
            "required_when" in q and evaluate(q["required_when"], raw)
        )
        if q["type"] == "boolean":
            value = bool(value) if value is not None else (False if not required else None)
        if value in (None, ""):
            if required:
                errors[qid] = "This field is required"
            continue
        if q["type"] == "select" and value not in {o["value"] for o in q["options"]}:
            errors[qid] = "Choose a valid option"
            continue
        for v in q.get("validators", []):
            if err := run_validator(v, value):
                errors[qid] = err
                break
        clean[qid] = value
    return clean, errors


def required_documents(answers: dict[str, Any]) -> list[dict]:
    out = []
    for d in DEFINITION["documents"]:
        if evaluate(d.get("visible_when"), answers):
            out.append({**d, "required": "required_when" in d and evaluate(d["required_when"], answers)})
    return out


def derive_priority(answers: dict[str, Any]) -> str:
    return "EXPEDITED" if _truthy(answers.get("is_urgent")) else "STANDARD"
