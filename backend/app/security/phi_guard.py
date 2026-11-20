"""CIP-PA-275 — Zone-3 PHI exclusion for event/messaging data.

Two layers, both must pass:
1. **Structural allowlist** — every event type declares exactly which keys may appear.
   Anything else is rejected *by construction*, so a developer adding `member_dob` to a
   payload gets a hard failure in tests rather than a leak in production.
2. **Content scan** — defence in depth: values are scanned for PHI-shaped data
   (SSN, dates of birth, phone, email, street addresses) in case an allowed field is misused.
"""
from __future__ import annotations

import re
from typing import Any

EVENT_ALLOWLIST: dict[str, frozenset[str]] = {
    "DocumentIngested": frozenset(
        {"event_id", "dcn", "transaction_id", "document_ids", "request_type", "service_category",
         "priority", "member_token", "document_count", "occurred_at"}
    ),
    "DocumentClassified": frozenset(
        {"event_id", "dcn", "transaction_id", "document_id", "doc_class", "confidence", "occurred_at"}
    ),
    "DocumentExtracted": frozenset(
        {"event_id", "dcn", "transaction_id", "document_id", "icd10_codes", "cpt_codes", "occurred_at"}
    ),
    "IntegrityViolation": frozenset(
        {"event_id", "dcn", "transaction_id", "document_id", "stage", "occurred_at"}
    ),
    "SlaStateChanged": frozenset(
        {"event_id", "dcn", "transaction_id", "sla_event", "policy_code", "policy_version", "occurred_at"}
    ),
    "ReviewDecided": frozenset(
        {"event_id", "dcn", "transaction_id", "decision", "occurred_at"}
    ),
}

PHI_PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "dob": re.compile(r"\b(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b(?!T)"),
    "phone": re.compile(r"\b\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "street_address": re.compile(
        r"\b\d{1,5}\s+\w+(\s\w+)*\s(Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Blvd)\b", re.I
    ),
}


class PHIViolation(ValueError):
    def __init__(self, event_type: str, problems: list[str]):
        self.event_type = event_type
        self.problems = problems
        super().__init__(f"PHI guard rejected {event_type}: {'; '.join(problems)}")


def _walk(value: Any, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out += _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            out += _walk(v, f"{path}[{i}]")
    elif isinstance(value, str):
        out.append((path, value))
    return out


def scan_text(text: str) -> list[str]:
    return [name for name, rx in PHI_PATTERNS.items() if rx.search(text)]


def inspect_payload(event_type: str, payload: dict[str, Any]) -> list[str]:
    """Return a list of problems (empty == safe). Never raises — useful for UI testers."""
    problems: list[str] = []
    allowed = EVENT_ALLOWLIST.get(event_type)
    if allowed is None:
        return [f"event type '{event_type}' is not registered in the allowlist"]
    for key in payload:
        if key not in allowed:
            problems.append(f"field '{key}' is not allowlisted for {event_type}")
    for path, text in _walk(payload):
        if path.endswith("occurred_at"):
            continue  # ISO timestamps are operational metadata, not DOBs
        for hit in scan_text(text):
            problems.append(f"value at '{path}' looks like {hit}")
    return problems


def enforce(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    problems = inspect_payload(event_type, payload)
    if problems:
        raise PHIViolation(event_type, problems)
    return payload
