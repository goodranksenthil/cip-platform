"""Smart intake: read clinical documents (PDF / Word .docx) and pre-fill the questionnaire.

Pipeline per file:
    bytes -> text (pypdf | OOXML parser) -> classify (existing model) -> field extractors
Each extracted field carries a confidence, the file it came from and an evidence snippet, so
the reviewer can see *why* a value was filled. Nothing here is authoritative: the result is
re-validated by the questionnaire engine and confirmed by a human before ingestion.

Word support is dependency-free: a .docx is a ZIP; we read word/document.xml with a size
guard (zip-bomb protection) and walk paragraphs / table cells.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, BinaryIO
from xml.etree import ElementTree as ET

from app.services import extraction, questionnaire

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# --------------------------------------------------------------------------- text extraction
def docx_text(stream: BinaryIO) -> str:
    try:
        with zipfile.ZipFile(stream) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise ValueError("Word document body is too large")
            root = ET.fromstring(z.read(info))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("Not a valid Word (.docx) document") from exc
    lines: list[str] = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{_W}tab",):
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def is_docx(head: bytes, filename: str) -> bool:
    return head.startswith(b"PK\x03\x04") and filename.lower().endswith(".docx")


def read_text(data: bytes, mime: str) -> str:
    if mime == DOCX_MIME:
        return docx_text(io.BytesIO(data))
    if mime == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages[:50])
    if mime == "text/plain":
        return data.decode("utf-8", errors="replace")
    return ""


# --------------------------------------------------------------------------- field extraction
@dataclass
class Found:
    value: Any
    confidence: float
    source: str
    evidence: str


def _snip(text: str, m: re.Match) -> str:
    s, e = max(0, m.start() - 30), min(len(text), m.end() + 30)
    return " ".join(text[s:e].split())


DATE_RX = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"


def parse_date(raw: str) -> str | None:
    raw = raw.strip().replace(",", "")
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", raw)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        day, month = (a, b) if a > 12 else (b, a) if b > 12 else (a, b)  # default day-first (India)
        try:
            return date(y, month, day).isoformat()
        except ValueError:
            return None
    return None


def _first(rx: str, text: str, src: str, conf: float, group: int = 1, flags=re.I, post=None) -> Found | None:
    m = re.search(rx, text, flags)
    if not m:
        return None
    val = m.group(group).strip()
    if post:
        val = post(val)
        if val in (None, ""):
            return None
    return Found(val, conf, src, _snip(text, m))


LABEL = r"[ \t]*[:#\-][ \t]*"


def extract_fields(text: str, src: str) -> dict[str, Found]:
    f: dict[str, Found] = {}
    low = text.lower()

    def put(key: str, found: Found | None):
        if found and (key not in f or found.confidence > f[key].confidence):
            f[key] = found

    # ---- member & provider (PHI stays in this request/response only; never persisted here)
    put("member_name", _first(rf"(?:patient|member)(?:'s)?\s*(?:full\s*)?name{LABEL}([A-Z][A-Za-z.'\- ]{{2,60}}?)\s*(?:\n|$|\s{{2,}}|,|\t|DOB|Date)", text, src, 0.9))
    put("member_dob", _first(rf"(?:DOB|D\.O\.B\.?|date\s*of\s*birth){LABEL}{DATE_RX}", text, src, 0.9, post=parse_date))
    put("member_id", _first(rf"(?:member|subscriber|policy)\s*(?:id|no\.?|number){LABEL}([A-Z]\d{{9}})\b", text, src, 0.95, post=str.upper))
    put("member_address", _first(rf"(?:member\s*|patient\s*|home\s*)?address{LABEL}([^\n]{{8,160}})", text, src, 0.8))
    put("provider_npi", _first(rf"NPI(?:\s*(?:no\.?|number))?{LABEL}?\s*(\d{{10}})\b", text, src, 0.95,
                               post=lambda v: v if questionnaire.npi_is_valid(v) else None))

    codes = extraction.extract_codes(text)
    if codes["icd10_codes"]:
        f["icd10_codes"] = Found(", ".join(codes["icd10_codes"]), 0.85, src, "ICD-10 pattern matches in document")
    if codes["cpt_codes"]:
        f["cpt_codes"] = Found(", ".join(codes["cpt_codes"]), 0.85, src, "CPT codes following a 'CPT' label")

    # ---- request details
    rt = None
    if m := re.search(r"\b(appeal|adverse determination|denial|denied)\b", low):
        rt = Found("APPEAL", 0.8, src, _snip(text, m))
    elif m := re.search(r"\b(retrospective|retro review|post[- ]service)\b", low):
        rt = Found("RETRO_REVIEW", 0.8, src, _snip(text, m))
    elif m := re.search(r"\b(concurrent review|continued stay|extension of stay)\b", low):
        rt = Found("CONCURRENT_REVIEW", 0.8, src, _snip(text, m))
    elif m := re.search(r"\b(prior auth\w*|pre[- ]?auth\w*|request for authori[sz]ation|referral)\b", low):
        rt = Found("PRIOR_AUTH", 0.75, src, _snip(text, m))
    put("request_type", rt)

    svc_rules = [
        ("IMAGING", r"\b(mri|ct scan|ct|pet scan|imaging|radiology)\b"),
        ("OUTPATIENT_SURGERY", r"\b(surgery|arthroscop\w*|laparoscop\w*|day[- ]case|meniscectomy)\b"),
        ("INPATIENT", r"\b(inpatient|admission|admitted|length of stay)\b"),
        ("DME", r"\b(cpap|bipap|wheelchair|durable medical equipment|dme)\b"),
        ("BEHAVIORAL_HEALTH", r"\b(depress\w*|psychiatr\w*|behaviou?ral health|psychotherapy)\b"),
    ]
    # The "Plan:" line states what is being requested; findings elsewhere (e.g. "MRI confirms a
    # tear") are history. Search the plan first, then fall back to the whole document.
    plan_lines = "\n".join(l for l in text.splitlines() if re.match(r"\s*(plan|requested service|request(ed)?)\b", l, re.I))
    for scope, conf in ((plan_lines, 0.85), (text, 0.7)):
        hit = next(((code, m) for code, rx in svc_rules if (m := re.search(rx, scope.lower()))), None)
        if hit:
            put("service_category", Found(hit[0], conf, src, _snip(scope, hit[1])))
            break
    for scope, conf in ((plan_lines, 0.85), (text, 0.7)):
        if m := re.search(r"\b(mri|ct|pet)\b", scope.lower()):
            put("imaging_modality", Found(m.group(1).upper(), conf, src, _snip(scope, m)))
            break

    if m := re.search(r"\b(urgent|expedited|jeopardi[sz]e|emergent)\b", low):
        f["is_urgent"] = Found(True, 0.8, src, _snip(text, m))
        sent = re.search(r"[^.\n]*\b(urgent|expedited|jeopardi[sz]e|deficit|risk)\b[^.\n]*[.\n]", text, re.I)
        if sent and len(sent.group(0).strip()) >= 30:
            f["urgency_justification"] = Found(sent.group(0).strip()[:400], 0.6, src, "sentence mentioning urgency")

    put("original_auth_number", _first(r"\b(PA-\d{6})\b", text, src, 0.95))
    for code, rx in [("MEDICAL_NECESSITY", r"not medically necessary|medical necessity"),
                     ("INSUFFICIENT_DOCS", r"insufficient (?:clinical )?documentation"),
                     ("OUT_OF_NETWORK", r"out[- ]of[- ]network"),
                     ("EXPERIMENTAL", r"experimental|investigational")]:
        if m := re.search(rx, low):
            put("denial_reason", Found(code, 0.8, src, _snip(text, m)))
            break
    put("conservative_therapy_weeks", _first(
        r"(\d{1,2})\s*weeks?\s*(?:of\s*)?(?:conservative|physio\w*|physical therapy|therapy|nsaids?)", text, src, 0.75, post=int)
        or _first(r"(?:failed|completed)\s*(\d{1,2})\s*weeks?", text, src, 0.65, post=int))
    put("admission_date", _first(rf"admission\s*date{LABEL}{DATE_RX}", text, src, 0.85, post=parse_date))
    put("length_of_stay", _first(r"(?:length of stay|LOS)\D{0,12}(\d{1,3})\s*days?", text, src, 0.8, post=int))
    put("date_of_service", _first(rf"date\s*of\s*service{LABEL}{DATE_RX}", text, src, 0.85, post=parse_date))
    if m := re.search(r"\b(rental|rent|purchase|buy)\b", low):
        put("dme_acquisition", Found("RENTAL" if m.group(1).startswith("rent") else "PURCHASE", 0.6, src, _snip(text, m)))
    return f


CLASS_TO_SLOT = {
    "CLINICAL_NOTE": "clinical_notes", "DENIAL_LETTER": "denial_letter", "IMAGING_ORDER": "imaging_order",
    "ATTESTATION": "physician_attestation", "DISCHARGE_SUMMARY": "discharge_summary",
}

# One provider letter often carries several required documents at once (clinical note + imaging
# order + urgency attestation). The classifier picks a single best class, so each slot also gets
# its own evidence test; a file is suggested for every slot its text supports.
_IMAGING = r"\b(mri|ct|pet|imaging|radiology|x-?ray|ultrasound)\b"
_ORDER_LINE = r"^\s*(plan|order(ed)?|requested service|request(ed)?)\b"


def _has_imaging_order(text: str) -> bool:
    if re.search(r"\b(imaging|radiology)\s+order\b", text, re.I):
        return True
    return any(re.search(_IMAGING, line, re.I) for line in text.splitlines() if re.match(_ORDER_LINE, line, re.I))


SLOT_EVIDENCE = {
    "clinical_notes": lambda t: bool(re.search(r"history of present illness|progress note|assessment\s*:|subjective\s*:", t, re.I)),
    "denial_letter": lambda t: bool(re.search(r"\b(denied|denial|adverse determination|not medically necessary|appeal rights)\b", t, re.I)),
    "imaging_order": _has_imaging_order,
    "physician_attestation": lambda t: bool(re.search(r"\battest\w*|\bi certify\b|\bjeopardi[sz]e|\burgent review requested\b", t, re.I)),
    "discharge_summary": lambda t: bool(re.search(r"\bdischarge summary\b|\bhospital course\b", t, re.I)),
}


def suggest_slots(primary: str, text: str, applicable: set[str]) -> list[str]:
    """Primary slot first, then every other applicable slot the text gives evidence for."""
    slots = [primary] if primary in applicable else []
    slots += [code for code, has in SLOT_EVIDENCE.items() if code in applicable and code not in slots and has(text)]
    return slots or ["other"]


def analyse(files: list[tuple[str, str, str, bytes]]) -> dict:
    """files = [(file_id, filename, mime, bytes)] -> merged field suggestions + per-file info."""
    merged: dict[str, Found] = {}
    per_file = []
    texts: list[str] = []
    for file_id, filename, mime, data in files:
        try:
            text = read_text(data, mime)
            error = None
        except Exception as exc:  # unreadable / encrypted PDF etc.
            text, error = "", str(exc)[:200]
        texts.append(text)
        doc_class, conf = extraction.classify(text, "") if text.strip() else ("UNCLASSIFIED", 0.0)
        fields = extract_fields(text, filename) if text else {}
        for k, v in fields.items():
            if k not in merged or v.confidence > merged[k].confidence:
                merged[k] = v
        per_file.append({"file_id": file_id, "filename": filename, "content_type": mime, "doc_class": doc_class,
                         "confidence": conf, "suggested_slot": CLASS_TO_SLOT.get(doc_class, "other"),
                         "characters": len(text), "fields_found": sorted(fields), "error": error,
                         "_clinical": bool(re.search(r"history of present illness|progress note|assessment\s*:", text, re.I))})

    answers: dict[str, Any] = {k: v.value for k, v in merged.items()}
    clinical = [p.pop("_clinical", False) for p in per_file]
    if any(p["doc_class"] == "CLINICAL_NOTE" for p in per_file) or any(clinical):
        answers["has_clinical_notes"] = True
        merged.setdefault("has_clinical_notes", Found(True, 0.9, per_file[0]["filename"], "a clinical note was uploaded"))
    answers.setdefault("is_urgent", False)
    applicable = {d["code"] for d in questionnaire.required_documents(answers)}
    for p, text in zip(per_file, texts):
        p["suggested_slots"] = suggest_slots(p["suggested_slot"], text, applicable) if text.strip() else ["other"]
    return {
        "answers": answers,
        "fields": {k: asdict(v) for k, v in merged.items()},
        "files": per_file,
    }
