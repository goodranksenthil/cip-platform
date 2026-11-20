"""Cloud deployment pieces: Supabase Storage blob backend and Supabase connection strings."""
import io
import json

import httpx
import pytest

from app.core.config import sqlalchemy_url
from app.security.hashing import new_sha256
from app.services.storage import SupabaseBlobStore, UploadTooLarge

BASE = "https://proj.supabase.co"


class FakeSupabaseStorage:
    """Just enough of the Supabase Storage REST API for SupabaseBlobStore."""

    def __init__(self):
        self.buckets: dict[str, dict] = {}
        self.objects: dict[str, bytes] = {}
        self.auth: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.auth.append(req.headers.get("authorization", ""))
        path = req.url.path.removeprefix("/storage/v1")
        if path.startswith("/bucket"):
            if req.method == "GET":
                name = path.removeprefix("/bucket/")
                return httpx.Response(200 if name in self.buckets else 404, json={})
            body = json.loads(req.content)
            self.buckets[body["id"]] = body
            return httpx.Response(200, json={"name": body["id"]})
        key = path.removeprefix("/object/")
        if req.method == "POST":
            self.objects[key] = req.content
            return httpx.Response(200, json={"Key": key})
        if req.method == "GET":
            return httpx.Response(200, content=self.objects[key]) if key in self.objects else \
                httpx.Response(400, json={"error": "not_found"})
        if req.method == "DELETE":
            bucket = key
            for p in json.loads(req.content)["prefixes"]:
                self.objects.pop(f"{bucket}/{p}", None)
            return httpx.Response(200, json=[])
        return httpx.Response(405)


@pytest.fixture
def fake():
    return FakeSupabaseStorage()


@pytest.fixture
def store(fake):
    return SupabaseBlobStore(BASE, "service-key", "cip-docs", client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_bucket_is_created_private(fake, store):
    assert fake.buckets["cip-docs"]["public"] is False
    assert all(a == "Bearer service-key" for a in fake.auth)


def test_put_open_metadata_append_delete_roundtrip(fake, store):
    data = b"%PDF-1.7 hello" * 10_000
    blob = store.put_stream(io.BytesIO(data), "case 5.pdf")
    h = new_sha256()
    h.update(data)
    assert blob.sha256 == h.hexdigest() and blob.size == len(data) and blob.head.startswith(b"%PDF")
    assert blob.path == f"source/{blob.sha256[:2]}/{blob.sha256}/case 5.pdf"  # same layout as local store

    with store.open(blob.path) as fh:
        assert fh.read() == data
    store.set_metadata(blob.path, {"sha256": blob.sha256, "dcn": "DCN-1"})
    assert store.get_metadata(blob.path) == {"sha256": blob.sha256, "dcn": "DCN-1"}

    store.append(blob.path, b"[tampered]")
    with store.open(blob.path) as fh:
        assert fh.read().endswith(b"[tampered]")

    store.delete(blob.path)
    assert not store.exists(blob.path)
    assert store.get_metadata(blob.path) == {}
    with pytest.raises(FileNotFoundError):
        store.open(blob.path)


def test_size_limit_is_enforced_before_upload(fake, store):
    with pytest.raises(UploadTooLarge):
        store.put_stream(io.BytesIO(b"x" * 2048), "big.pdf", max_bytes=1024)
    assert not fake.objects


def test_missing_credentials_fail_fast():
    with pytest.raises(RuntimeError, match="CIP_SUPABASE_URL"):
        SupabaseBlobStore("", "", "b", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))


@pytest.mark.parametrize("raw, expected", [
    ("postgresql://postgres.ref:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres",
     "postgresql+psycopg://postgres.ref:pw@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require"),
    ("postgres://postgres:pw@db.ref.supabase.co:5432/postgres?application_name=cip",
     "postgresql+psycopg://postgres:pw@db.ref.supabase.co:5432/postgres?application_name=cip&sslmode=require"),
    ("postgresql+psycopg://u:p@x.supabase.com:5432/db?sslmode=verify-full",
     "postgresql+psycopg://u:p@x.supabase.com:5432/db?sslmode=verify-full"),
    ("postgresql+psycopg://cip:cip_password@localhost:5432/cip_db",
     "postgresql+psycopg://cip:cip_password@localhost:5432/cip_db"),
])
def test_supabase_connection_strings_are_normalised(raw, expected):
    assert sqlalchemy_url(raw) == expected
