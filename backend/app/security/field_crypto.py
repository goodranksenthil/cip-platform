"""CIP-PA-274 — Zone-2 column encryption with pgcrypto.

Encryption and decryption happen *inside Postgres* (`pgp_sym_encrypt`/`pgp_sym_decrypt`),
so plaintext PHI never sits in an ORM attribute longer than needed and ciphertext is what
lands on disk, in backups and in replicas. Decryption is gated by role and always audited.
"""
from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AccessAudit, Member

ROLES_ALLOWED_TO_DECRYPT = {"clinical_reviewer", "medical_director"}
_CIPHER_OPTS = "cipher-algo=aes256, compress-algo=1"


def encrypt_expr(value: str):
    """SQL expression — evaluated by Postgres at INSERT time."""
    return func.pgp_sym_encrypt(value, get_settings().field_encryption_key, _CIPHER_OPTS)


def mask(value: str, kind: str) -> str:
    if kind == "dob":
        return "****-**-" + value[-2:] if len(value) >= 2 else "****"
    if kind == "name":
        return " ".join(p[0] + "•" * (len(p) - 1) for p in value.split() if p)
    if kind == "address":
        return "•••• " + value.split(",")[-1].strip() if "," in value else "••••••"
    if kind == "member_id":
        return "•" * max(len(value) - 4, 0) + value[-4:]
    return "••••"


def decrypt_member(db: Session, member: Member, role: str, purpose: str) -> dict:
    allowed = role in ROLES_ALLOWED_TO_DECRYPT
    db.add(AccessAudit(actor_role=role, action="DECRYPT", resource=f"member:{member.member_token}",
                       allowed=allowed, purpose=purpose[:200] if purpose else None))
    key = get_settings().field_encryption_key
    row = db.execute(
        select(
            func.pgp_sym_decrypt(Member.member_name_enc, key),
            func.pgp_sym_decrypt(Member.member_dob_enc, key),
            func.pgp_sym_decrypt(Member.member_addr_enc, key),
            func.pgp_sym_decrypt(Member.member_id_enc, key),
        ).where(Member.id == member.id)
    ).one()
    db.commit()
    name, dob, addr, mid = row
    if allowed:
        return {"name": name, "dob": dob, "address": addr, "member_id": mid, "masked": False}
    return {"name": mask(name, "name"), "dob": mask(dob, "dob"), "address": mask(addr, "address"),
            "member_id": mask(mid, "member_id"), "masked": True}


def ciphertext_preview(db: Session, member: Member) -> dict:
    """Show what is physically stored — proves the column is ciphertext."""
    row = db.execute(
        text("SELECT encode(substring(member_dob_enc from 1 for 24), 'hex'), octet_length(member_dob_enc) "
             "FROM members WHERE id = :id"), {"id": member.id}
    ).one()
    return {"dob_ciphertext_prefix_hex": row[0], "dob_ciphertext_bytes": row[1]}
