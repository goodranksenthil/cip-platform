"""Database engine, session factory and schema bootstrap (incl. immutability triggers)."""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings, sqlalchemy_url

settings = get_settings()

_url = sqlalchemy_url(settings.database_url)
# Supabase's *transaction* pooler (port 6543) cannot keep server-side prepared statements;
# psycopg 3 prepares automatically, so turn that off there. Session pooler / direct are fine.
_connect_args = {"prepare_threshold": None} if ":6543/" in _url else {}
engine = create_engine(_url, pool_pre_ping=True, pool_size=settings.db_pool_size,
                       max_overflow=settings.db_max_overflow, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Unit-of-work for background workers: commit on success, rollback on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --------------------------------------------------------------------------------------
# Database-level guarantees. These live in Postgres, not just in Python, so that no code
# path (or ad-hoc SQL) can silently rewrite the SLA clock or the audit trail.
# --------------------------------------------------------------------------------------
DDL_PRE = [
    "CREATE EXTENSION IF NOT EXISTS pgcrypto",
    "CREATE SEQUENCE IF NOT EXISTS dcn_seq START 1",
]

DDL_POST = [
    # CIP-PA-005: immutable intake timestamps on the master transaction record
    """
    CREATE OR REPLACE FUNCTION cip_guard_txn_timestamps() RETURNS trigger AS $$
    BEGIN
      IF NEW.source_received_at IS DISTINCT FROM OLD.source_received_at
         OR NEW.cip_received_at IS DISTINCT FROM OLD.cip_received_at
         OR NEW.created_at     IS DISTINCT FROM OLD.created_at
         OR NEW.sla_started_at IS DISTINCT FROM OLD.sla_started_at
         OR NEW.dcn            IS DISTINCT FROM OLD.dcn THEN
        RAISE EXCEPTION 'CIP-PA-005: intake timestamps / DCN are immutable (txn %)', OLD.id
          USING ERRCODE = 'integrity_constraint_violation';
      END IF;
      RETURN NEW;
    END $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_guard_txn_timestamps ON transactions",
    """CREATE TRIGGER trg_guard_txn_timestamps BEFORE UPDATE ON transactions
       FOR EACH ROW EXECUTE FUNCTION cip_guard_txn_timestamps()""",
    # Append-only ledgers: SLA lifecycle events & integrity checks are never edited
    """
    CREATE OR REPLACE FUNCTION cip_append_only() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
    END $$ LANGUAGE plpgsql
    """,
    "DROP TRIGGER IF EXISTS trg_sla_events_append_only ON sla_events",
    """CREATE TRIGGER trg_sla_events_append_only BEFORE UPDATE OR DELETE ON sla_events
       FOR EACH ROW EXECUTE FUNCTION cip_append_only()""",
    "DROP TRIGGER IF EXISTS trg_integrity_append_only ON integrity_checks",
    """CREATE TRIGGER trg_integrity_append_only BEFORE UPDATE OR DELETE ON integrity_checks
       FOR EACH ROW EXECUTE FUNCTION cip_append_only()""",
    # Fast outbox polling: partial index on unpublished rows only
    """CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox_events (next_attempt_at)
       WHERE status = 'PENDING'""",
]


def init_db() -> None:
    from app import models  # noqa: F401  (register mappers)

    with engine.begin() as conn:
        for stmt in DDL_PRE:
            conn.execute(text(stmt))
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in DDL_POST:
            conn.execute(text(stmt))
        # Row Level Security ON with no policies: on Supabase, the public Data API (anon /
        # authenticated keys via PostgREST) can then read nothing from these PHI tables. The
        # app connects as the table owner, which bypasses non-forced RLS, so it is unaffected.
        for table in Base.metadata.sorted_tables:
            conn.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
