"""Centralised, environment-driven configuration (12-factor style)."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CIP_", extra="ignore")

    app_name: str = "CIP — Clinical Intake Platform"
    environment: str = "local"

    database_url: str = "postgresql+psycopg://cip:cip_password@localhost:5432/cip_db"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # Zone-2 field encryption key (pgcrypto pgp_sym_encrypt). In production this
    # comes from Azure Key Vault / KMS — never from source control.
    field_encryption_key: str = Field(default="change-me-dev-only-key", min_length=8)

    # Blob storage: "local" (filesystem, simulates Azure Blob) or "supabase" (Supabase Storage).
    # Hosts with an ephemeral disk (Render, containers) must use "supabase".
    storage_backend: str = "local"
    blob_root: Path = Path("./storage/blob")
    supabase_url: str = ""                # https://<project-ref>.supabase.co
    supabase_service_role_key: str = ""   # server-side only; never ship to the browser
    supabase_bucket: str = "cip-documents"
    max_upload_bytes: int = 25 * 1024 * 1024
    hash_chunk_size: int = 64 * 1024  # CIP-PA-025: 64KB streaming chunks

    # Background workers (outbox relay + consumers)
    workers_enabled: bool = True
    relay_interval_seconds: float = 2.0
    relay_batch_size: int = 50
    relay_max_attempts: int = 5

    # Optional real Kafka. When empty, a Postgres-backed log broker with the same
    # semantics (topics, offsets, consumer groups, manual commit) is used.
    kafka_bootstrap_servers: str = ""

    prompt_max_chars: int = 2000  # CIP-PA-315
    seed_demo_data: bool = True
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    # Optional regex for extra origins, e.g. Vercel preview deployments:
    # ^https://cip-[a-z0-9-]+\.vercel\.app$
    cors_origin_regex: str | None = None


def sqlalchemy_url(url: str) -> str:
    """Accept the plain URI Supabase shows (postgres:// or postgresql://) and pin the psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url[len(prefix):]
    if ".supabase.co" in url or ".supabase.com" in url:
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
