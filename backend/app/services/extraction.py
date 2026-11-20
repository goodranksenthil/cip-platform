"""Classify & Extract (pipeline steps 4 and 5).

* Text extraction streams through pypdf for PDFs; plain text is decoded directly.
* Classification is an explainable weighted-keyword model (swap in an ML model or the
  AI gateway's `document_classification` capability behind the same function).
* Extraction emits only de-identified clinical codes (ICD-10, CPT) — never names/dates.
* The extracted text is stored as a DERIVATIVE artifact with its own SHA-256 and a
  `parent_document_id` link, giving source→derivative lineage (CIP-PA-003).
"""
from __future__ import annotations

import io
import re
from collections import Counter

from sqlalchemy.orm import Session

from app.models import ArtifactKind, Document, IntegrityState
from app.services.storage import get_store

CLASS_KEYWORDS: dict[str, dict[str, float]] = {
    "CLINICAL_NOTE": {"history of present illness": 3, "assessment": 2, "plan": 1, "progress note": 3, "exam": 1, "subjective": 2},
    "DENIAL_LETTER": {"denied": 3, "denial": 3, "appeal rights": 3, "not medically necessary": 3, "adverse determination": 3},
    "IMAGING_ORDER": {"order": 2, "mri": 2, "ct scan": 2, "imaging": 2, "radiology": 2, "contrast": 1},
    "ATTESTATION": {"attest": 3, "certify": 2, "urgent": 2, "jeopardize": 3, "signature": 1},
    "DISCHARGE_SUMMARY": {"discharge": 3, "admission date": 2, "hospital course": 3, "disposition": 2},
    "LAB_RESULT": {"reference range": 3, "specimen": 2, "result": 1, "lab": 1},
}

ICD10_RX = re.compile(r"\b([A-TV-Z]\d[0-9A-Z](?:\.[0-9A-Z]{1,4})?)\b")
CPT_RX = re.compile(r"\b(?:CPT[:\s#]*)?(\d{5})\b")
CPT_CONTEXT_RX = re.compile(r"CPT[:\s#]*((?:\d{5}[,\s]*)+)", re.I)


def extract_text(doc: Document, max_chars: int = 200_000) -> str:
    store = get_store()
    if doc.content_type == "text/plain":
        with store.open(doc.blob_path) as fh:
            return fh.read(max_chars).decode("utf-8", errors="replace")
    if doc.content_type == "application/pdf":
        from pypdf import PdfReader

        with store.open(doc.blob_path) as fh:
            reader = PdfReader(io.BytesIO(fh.read()))
            parts: list[str] = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
                if sum(map(len, parts)) > max_chars:
                    break
            return "\n".join(parts)[:max_chars]
    if doc.content_type.startswith("application/vnd.openxmlformats-officedocument.wordprocessingml"):
        from app.services.document_reader import docx_text

        with store.open(doc.blob_path) as fh:
            return docx_text(fh)[:max_chars]
    return ""  # images: OCR would plug in here


def classify(text: str, requirement_code: str) -> tuple[str, float]:
    lower = text.lower()
    scores: Counter[str] = Counter()
    for cls, kws in CLASS_KEYWORDS.items():
        for kw, w in kws.items():
            scores[cls] += w * lower.count(kw)
    prior = {"clinical_notes": "CLINICAL_NOTE", "denial_letter": "DENIAL_LETTER", "imaging_order": "IMAGING_ORDER",
             "physician_attestation": "ATTESTATION", "discharge_summary": "DISCHARGE_SUMMARY"}.get(requirement_code)
    if prior:
        scores[prior] += 2  # the slot the user uploaded into is evidence too
    if not scores or scores.most_common(1)[0][1] == 0:
        return "UNCLASSIFIED", 0.0
    (best, top), total = scores.most_common(1)[0], sum(scores.values())
    return best, round(min(0.99, top / total), 2)


def extract_codes(text: str) -> dict[str, list[str]]:
    icd = sorted({c for c in ICD10_RX.findall(text) if not c.isdigit()})
    cpt: set[str] = set()
    for group in CPT_CONTEXT_RX.findall(text):
        cpt.update(re.findall(r"\d{5}", group))
    return {"icd10_codes": icd[:25], "cpt_codes": sorted(cpt)[:25]}


def create_derivative(db: Session, source: Document, text: str) -> Document:
    store = get_store()
    blob = store.put_bytes(text.encode("utf-8"), f"{source.original_filename}.txt", container="derived")
    child = Document(transaction_id=source.transaction_id, parent_document_id=source.id,
                     artifact_kind=ArtifactKind.DERIVATIVE_TEXT, requirement_code=source.requirement_code,
                     original_filename=f"{source.original_filename}.txt", content_type="text/plain",
                     size_bytes=blob.size, sha256=blob.sha256, blob_path=blob.path,
                     integrity_state=IntegrityState.VERIFIED, doc_class=source.doc_class)
    db.add(child)
    db.flush()
    store.set_metadata(blob.path, {"sha256": blob.sha256, "document_id": child.id, "parent_sha256": source.sha256,
                                   "artifact_kind": "DERIVATIVE_TEXT"})
    return child
