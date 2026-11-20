import os
import tempfile

# Must be set before the app modules read settings
os.environ.setdefault("CIP_DATABASE_URL", "postgresql+psycopg://cip:cip_password@localhost:5432/cip_test")
os.environ["CIP_SEED_DEMO_DATA"] = "false"
os.environ["CIP_WORKERS_ENABLED"] = "false"
os.environ["CIP_BLOB_ROOT"] = tempfile.mkdtemp(prefix="cip-blob-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import Base, engine, init_db  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    init_db()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(scope="session")
def client(database):
    from app.main import app

    with TestClient(app) as c:
        yield c
