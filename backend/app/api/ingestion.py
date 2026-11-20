"""Document Ingestion API — condition-based questionnaire + multipart upload."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.serializers import txn_detail
from app.core.db import get_db
from app.services import questionnaire, sla
from app.services.ingestion import IncomingFile, IngestionError, ingest

router = APIRouter(prefix="/api/ingestion", tags=["Document Ingestion"])


@router.get("/questionnaire")
def get_questionnaire():
    return questionnaire.DEFINITION


class EvaluateIn(BaseModel):
    answers: dict


@router.post("/evaluate")
def evaluate(body: EvaluateIn, db: Session = Depends(get_db)):
    """Server-side preview: which documents are required and which SLA policy will apply."""
    clean, errors = questionnaire.validate_answers(body.answers)
    priority = questionnaire.derive_priority(body.answers)
    policy = None
    if body.answers.get("request_type"):
        try:
            p = sla.resolve_policy(db, body.answers["request_type"], priority)
            policy = {"code": p.code, "version": p.version, "target_hours": p.target_hours, "pausable": p.pausable,
                      "projected_due_at": (datetime.now(timezone.utc) + timedelta(hours=p.target_hours)).isoformat()}
        except LookupError:
            pass
    return {"errors": errors, "priority": priority, "sla_policy": policy,
            "documents": questionnaire.required_documents(body.answers)}


@router.post("/submit", status_code=201)
async def submit(request: Request, db: Session = Depends(get_db)):
    """Multipart: `answers` (JSON), `manifest` (JSON list of {field, filename, sha256}), files as `doc__<code>`."""
    form = await request.form(max_files=40, max_fields=50)
    try:
        answers = json.loads(form.get("answers") or "{}")
        manifest = {(m["field"], m["filename"]): m.get("sha256") for m in json.loads(form.get("manifest") or "[]")}
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(400, "answers/manifest must be valid JSON")

    files: list[IncomingFile] = []
    for field, value in form.multi_items():
        if field.startswith("doc__") and hasattr(value, "file"):
            code = field.removeprefix("doc__")
            files.append(IncomingFile(code, value.filename or "upload.bin", value.file,
                                      manifest.get((field, value.filename))))
    try:
        txn = ingest(db, answers, files)
    except IngestionError as e:
        raise HTTPException(e.status_code, {"message": e.message, "errors": e.field_errors})
    finally:
        await form.close()
    return txn_detail(txn)
