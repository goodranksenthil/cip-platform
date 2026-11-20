"""Upload Documents (smart intake) API — a separate workflow that feeds the SAME ingestion use-case.

1. POST /api/smart-intake/analyze   multipart files (+ manifest of browser SHA-256)
   -> files are streamed to a staging container (hashed in 64KB chunks, magic-byte checked),
      read (PDF / Word / text), classified and mined for questionnaire fields.
2. The user confirms / edits request details, member & provider, and document slots in the UI.
3. POST /api/smart-intake/submit    {batch_id, answers, assignments}
   -> staged files are handed to services.ingestion.ingest() exactly like the classic wizard,
      so DCN, SLA, encryption, outbox, integrity checks and audit behave identically.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.serializers import txn_detail
from app.core.db import get_db
from app.models import Document, StagedUpload
from app.security.hashing import digests_equal, is_valid_digest
from app.services import document_reader, questionnaire, sla
from app.services.ingestion import IncomingFile, IngestionError, ingest
from app.services.storage import UploadTooLarge, get_store, sniff_content_type

router = APIRouter(prefix="/api/smart-intake", tags=["Upload Documents"])

SMART_ACCEPT = {"application/pdf", document_reader.DOCX_MIME, "text/plain"}
EXTENSIONS = (".pdf", ".docx", ".txt")
MAX_FILES = 10


def _purge_old(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    old = db.scalars(select(StagedUpload).where(StagedUpload.created_at < cutoff)).all()
    for s in old:
        get_store().delete(s.blob_path)
        db.delete(s)
    db.commit()


@router.post("/analyze")
async def analyze(request: Request, db: Session = Depends(get_db)):
    form = await request.form(max_files=MAX_FILES + 1, max_fields=10)
    try:
        manifest = {m["filename"]: m.get("sha256") for m in json.loads(form.get("manifest") or "[]")}
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(400, "manifest must be valid JSON")
    uploads = [v for k, v in form.multi_items() if k == "files" and hasattr(v, "file")]
    if not uploads:
        raise HTTPException(422, "Upload at least one document")
    if len(uploads) > MAX_FILES:
        raise HTTPException(422, f"At most {MAX_FILES} documents per intake")

    _purge_old(db)
    store = get_store()
    batch_id = uuid.uuid4()
    staged: list[tuple[StagedUpload, bytes]] = []
    errors: dict[str, str] = {}
    try:
        for up in uploads:
            name = up.filename or "document"
            if name.lower().endswith(".doc"):
                errors[name] = "Legacy .doc is not supported — please save it as .docx and upload again"
                continue
            if not name.lower().endswith(EXTENSIONS):
                errors[name] = "Only PDF, Word (.docx) or .txt documents are accepted"
                continue
            try:
                blob = store.put_stream(up.file, name, container="staging")
            except UploadTooLarge as e:
                errors[name] = str(e)
                continue
            mime = sniff_content_type(blob.head, name)
            if mime not in SMART_ACCEPT:
                store.delete(blob.path)
                errors[name] = "File content does not match a PDF, Word (.docx) or text document"
                continue
            client = manifest.get(name)
            if client and (not is_valid_digest(client) or not digests_equal(client, blob.sha256)):
                store.delete(blob.path)
                errors[name] = "Browser SHA-256 does not match the bytes received — upload again"
                continue
            row = StagedUpload(batch_id=batch_id, filename=name, content_type=mime, size_bytes=blob.size,
                               sha256=blob.sha256, blob_path=blob.path)
            db.add(row)
            with store.open(blob.path) as fh:
                staged.append((row, fh.read()))
        db.commit()
    finally:
        await form.close()

    if not staged:
        raise HTTPException(422, {"message": "No readable documents", "errors": errors})

    result = document_reader.analyse([(str(r.id), r.filename, r.content_type, data) for r, data in staged])
    for f, (r, _) in zip(result["files"], staged):
        f.update({"sha256": r.sha256, "size_bytes": r.size_bytes})
    clean, field_errors = questionnaire.validate_answers(result["answers"])
    priority = questionnaire.derive_priority(result["answers"])
    policy = None
    if result["answers"].get("request_type"):
        try:
            p = sla.resolve_policy(db, result["answers"]["request_type"], priority)
            policy = {"code": p.code, "version": p.version, "target_hours": p.target_hours, "pausable": p.pausable}
        except LookupError:
            pass
    return {"batch_id": str(batch_id), **result, "validation_errors": field_errors, "rejected": errors,
            "priority": priority, "sla_policy": policy,
            "documents_required": questionnaire.required_documents(result["answers"])}


class Assignment(BaseModel):
    file_id: str
    requirement_code: str


class SubmitIn(BaseModel):
    batch_id: str
    answers: dict
    assignments: list[Assignment]


@router.post("/submit", status_code=201)
def submit(body: SubmitIn, db: Session = Depends(get_db)):
    try:
        batch = uuid.UUID(body.batch_id)
    except ValueError:
        raise HTTPException(400, "Invalid batch id")
    rows = {str(r.id): r for r in db.scalars(select(StagedUpload).where(StagedUpload.batch_id == batch))}
    if not rows:
        raise HTTPException(404, "Upload batch not found or expired — upload the documents again")
    store = get_store()
    streams = []
    files: list[IncomingFile] = []
    try:
        for a in body.assignments:
            row = rows.get(a.file_id)
            if row is None:
                raise HTTPException(400, f"Unknown file {a.file_id}")
            fh = store.open(row.blob_path)
            streams.append(fh)
            # staged hash becomes the client hash -> ingest() re-verifies end to end
            files.append(IncomingFile(a.requirement_code, row.filename, fh, row.sha256))
        txn = ingest(db, body.answers, files, accept_mime=SMART_ACCEPT)
    except IngestionError as e:
        raise HTTPException(e.status_code, {"message": e.message, "errors": e.field_errors})
    finally:
        for fh in streams:
            fh.close()

    # clean staging (blob only if no committed document shares the content-addressed path)
    for row in rows.values():
        if row.blob_path.startswith("staging") and not db.scalar(select(Document.id).where(Document.blob_path == row.blob_path)):
            store.delete(row.blob_path)
    db.execute(delete(StagedUpload).where(StagedUpload.batch_id == batch))
    db.commit()
    return txn_detail(txn)


@router.delete("/{batch_id}", status_code=204)
def discard(batch_id: str, db: Session = Depends(get_db)):
    try:
        batch = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(400, "Invalid batch id")
    for row in db.scalars(select(StagedUpload).where(StagedUpload.batch_id == batch)):
        get_store().delete(row.blob_path)
        db.delete(row)
    db.commit()
