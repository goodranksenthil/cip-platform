"""CIP-PA-314 / 315 — AI boundary controls.

* `sanitize_untrusted` treats user-supplied clinical text as data, never instructions:
  Unicode normalisation, invisible/control character stripping, injection-pattern
  detection and neutralisation, hard length cap (2000 chars).
* `build_context` is a *structural* allowlist: each AI capability declares the fields it
  may receive. Everything else — and anything PHI-shaped — is dropped and audited.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.security.phi_guard import scan_text

INJECTION_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("override_instructions", re.compile(r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b", re.I)),
    ("role_hijack", re.compile(r"\b(you are now|act as|pretend to be|from now on you)\b", re.I)),
    ("system_impersonation", re.compile(r"(^|\n)\s*(system|assistant|developer)\s*:", re.I)),
    ("tag_injection", re.compile(r"</?\s*(system|instructions?|prompt|context|im_start|im_end)[^>]*>", re.I)),
    ("exfiltration", re.compile(r"\b(reveal|print|show|output)\b.{0,30}\b(system prompt|api key|secret|password|instructions)\b", re.I)),
    ("decision_override", re.compile(r"\b(auto[- ]?approve|approve this (request|claim)|mark as approved|bypass (review|policy))\b", re.I)),
    ("code_fence", re.compile(r"```")),
]

_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class SanitizeResult:
    text: str
    original_length: int
    truncated: bool
    flags: list[str] = field(default_factory=list)
    removed_invisible: int = 0

    @property
    def risk(self) -> str:
        if any(f in self.flags for f in ("override_instructions", "role_hijack", "system_impersonation", "decision_override")):
            return "HIGH"
        return "MEDIUM" if self.flags else "LOW"


def sanitize_untrusted(text: str, max_chars: int = 2000) -> SanitizeResult:
    original_length = len(text)
    normalized = unicodedata.normalize("NFKC", text)
    invisible = len(_INVISIBLE.findall(normalized))
    cleaned = _CONTROL.sub("", _INVISIBLE.sub("", normalized))

    flags = [name for name, rx in INJECTION_RULES if rx.search(cleaned)]

    # Neutralise rather than silently delete, so reviewers can still read the content.
    neutral = cleaned
    for name, rx in INJECTION_RULES:
        if name == "code_fence":
            neutral = rx.sub("'''", neutral)
        elif name in ("tag_injection",):
            neutral = rx.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), neutral)
        elif name in ("system_impersonation",):
            neutral = rx.sub(lambda m: m.group(0).replace(":", " (quoted):"), neutral)
        elif name in flags:
            neutral = rx.sub(lambda m: f"[neutralised:{m.group(0)}]", neutral)

    truncated = len(neutral) > max_chars
    if truncated:
        neutral = neutral[:max_chars]
    return SanitizeResult(neutral, original_length, truncated, flags, invisible)


# Capability → exact context fields it may receive (minimum necessary)
AI_CAPABILITIES: dict[str, dict[str, Any]] = {
    "clinical_summary": {
        "description": "Summarise de-identified clinical codes for a reviewer",
        "allowed_fields": {"request_type", "service_category", "priority", "doc_classes", "icd10_codes", "cpt_codes", "reviewer_note"},
        "untrusted_fields": {"reviewer_note"},
    },
    "document_classification": {
        "description": "Suggest a document class from a sanitised excerpt",
        "allowed_fields": {"excerpt", "filename_extension"},
        "untrusted_fields": {"excerpt"},
    },
}


@dataclass
class ContextBuild:
    context: dict[str, Any]
    blocked_fields: list[str]
    injection_flags: list[str]
    truncated: bool
    blocked: bool


def build_context(capability: str, candidate: dict[str, Any], max_chars: int = 2000) -> ContextBuild:
    spec = AI_CAPABILITIES[capability]
    allowed: set[str] = spec["allowed_fields"]
    untrusted: set[str] = spec["untrusted_fields"]

    ctx: dict[str, Any] = {}
    blocked: list[str] = []
    flags: list[str] = []
    truncated = False

    for key, value in candidate.items():
        if key not in allowed:
            blocked.append(f"{key} (not authorised for {capability})")
            continue
        if isinstance(value, str):
            if key in untrusted:
                res = sanitize_untrusted(value, max_chars)
                value, truncated = res.text, truncated or res.truncated
                flags += res.flags
            phi = scan_text(value)
            if phi:
                blocked.append(f"{key} (contains {', '.join(phi)})")
                continue
        ctx[key] = value

    high_risk = any(f in flags for f in ("override_instructions", "role_hijack", "system_impersonation", "decision_override"))
    return ContextBuild(ctx, blocked, sorted(set(flags)), truncated, blocked=high_risk)
