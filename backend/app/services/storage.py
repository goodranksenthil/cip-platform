"""Object storage abstraction that mirrors Azure Blob semantics on the local filesystem.

* Content-addressed layout: `<container>/<sha[:2]>/<sha>/<filename>`
* Blob metadata (`x-ms-meta-*`) persisted in a sidecar JSON, exactly where the Azure SDK's
  `set_blob_metadata(...)` would put it. Swapping to `azure-storage-blob` means implementing
  the same four methods on an `AzureBlobStore` class.
* Writes are atomic: stream → temp file → fsync → rename.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

import httpx

from app.core.config import get_settings
from app.security.hashing import iter_chunks, new_sha256


@dataclass
class StoredBlob:
    path: str
    sha256: str
    size: int
    head: bytes  # first bytes, for magic-number sniffing


class UploadTooLarge(ValueError):
    pass


class LocalBlobStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or get_settings().blob_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ write
    def put_stream(self, stream: BinaryIO, filename: str, container: str = "source",
                   max_bytes: int | None = None, chunk_size: int | None = None) -> StoredBlob:
        """Single pass: hash + size + persist, never holding the whole file in memory."""
        settings = get_settings()
        max_bytes = max_bytes or settings.max_upload_bytes
        chunk_size = chunk_size or settings.hash_chunk_size
        h = new_sha256()
        size = 0
        head = b""
        tmp_dir = self.root / ".tmp"
        tmp_dir.mkdir(exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=tmp_dir)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in iter_chunks(stream, chunk_size):
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge(f"{filename} exceeds {max_bytes // (1024 * 1024)} MB")
                    if len(head) < 16:
                        head += chunk[: 16 - len(head)]
                    h.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            digest = h.hexdigest()
            safe_name = Path(filename).name.replace("/", "_")[:200] or "upload.bin"
            rel = Path(container) / digest[:2] / digest / safe_name
            dest = self.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, dest)
            return StoredBlob(str(rel), digest, size, head)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def put_bytes(self, data: bytes, filename: str, container: str = "derived") -> StoredBlob:
        import io
        return self.put_stream(io.BytesIO(data), filename, container)

    # ------------------------------------------------------------------ metadata
    def _meta_path(self, rel: str) -> Path:
        return (self.root / rel).with_name((self.root / rel).name + ".metadata.json")

    def set_metadata(self, rel: str, metadata: dict[str, str]) -> None:
        meta = {f"x-ms-meta-{k}": str(v) for k, v in metadata.items()}
        self._meta_path(rel).write_text(json.dumps(meta, indent=2))

    def get_metadata(self, rel: str) -> dict[str, str]:
        p = self._meta_path(rel)
        if not p.exists():
            return {}
        return {k.removeprefix("x-ms-meta-"): v for k, v in json.loads(p.read_text()).items()}

    # ------------------------------------------------------------------ read
    def open(self, rel: str) -> BinaryIO:
        return (self.root / rel).open("rb")

    def exists(self, rel: str) -> bool:
        return (self.root / rel).exists()

    def delete(self, rel: str) -> None:
        p = self.root / rel
        p.unlink(missing_ok=True)
        self._meta_path(rel).unlink(missing_ok=True)

    def absolute(self, rel: str) -> Path:
        return self.root / rel

    def append(self, rel: str, data: bytes) -> None:
        """DEMO ONLY: modify stored bytes in place (tamper simulation)."""
        with (self.root / rel).open("ab") as fh:
            fh.write(data)

    def wipe(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)


class SupabaseBlobStore:
    """Same interface as LocalBlobStore, backed by a private Supabase Storage bucket.

    * Same content-addressed keys, so `documents.blob_path` values are portable.
    * Hashing still happens here, in 64KB chunks, *before* upload (CIP-PA-025); the bytes are
      spooled to a temp file (memory up to 1 MB, then disk) so large files never sit in RAM.
    * Blob metadata lives in a sidecar object `<key>.metadata.json`, like the local store.
    * Uses the service-role key: this runs server-side only and the bucket is private.
    """

    def __init__(self, url: str | None = None, key: str | None = None, bucket: str | None = None,
                 client: httpx.Client | None = None):
        s = get_settings()
        self.url = (url or s.supabase_url).rstrip("/")
        key = key or s.supabase_service_role_key
        self.bucket = bucket or s.supabase_bucket
        if not self.url or not key:
            raise RuntimeError("CIP_STORAGE_BACKEND=supabase needs CIP_SUPABASE_URL and CIP_SUPABASE_SERVICE_ROLE_KEY")
        self.http = client or httpx.Client(timeout=60)
        # Legacy service_role keys are JWTs and go in both headers. New secret keys
        # (sb_secret_...) are not JWTs: Supabase accepts them only in `apikey` and mints
        # the JWT itself, so a Bearer header with them would be rejected.
        self.http.headers["apikey"] = key
        if key.startswith("eyJ"):
            self.http.headers["Authorization"] = f"Bearer {key}"
        self._ensure_bucket()

    # ------------------------------------------------------------------ helpers
    def _obj(self, rel: str) -> str:
        return f"{self.url}/storage/v1/object/{self.bucket}/{quote(rel)}"

    def _ensure_bucket(self) -> None:
        r = self.http.get(f"{self.url}/storage/v1/bucket/{self.bucket}")
        if r.status_code == 200:
            return
        r = self.http.post(f"{self.url}/storage/v1/bucket",
                           json={"id": self.bucket, "name": self.bucket, "public": False})
        if r.status_code not in (200, 201, 409) and "already exists" not in r.text.lower():
            raise RuntimeError(f"Could not create Supabase bucket {self.bucket}: {r.status_code} {r.text[:200]}")

    def _upload(self, rel: str, body: BinaryIO | bytes, content_type: str = "application/octet-stream") -> None:
        r = self.http.post(self._obj(rel), content=body,
                           headers={"Content-Type": content_type, "x-upsert": "true"})
        if r.status_code not in (200, 201):
            raise RuntimeError(f"Supabase upload failed for {rel}: {r.status_code} {r.text[:200]}")

    def _download(self, rel: str) -> bytes | None:
        r = self.http.get(self._obj(rel))
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.content

    # ------------------------------------------------------------------ write
    def put_stream(self, stream: BinaryIO, filename: str, container: str = "source",
                   max_bytes: int | None = None, chunk_size: int | None = None) -> StoredBlob:
        settings = get_settings()
        max_bytes = max_bytes or settings.max_upload_bytes
        chunk_size = chunk_size or settings.hash_chunk_size
        h = new_sha256()
        size = 0
        head = b""
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
            for chunk in iter_chunks(stream, chunk_size):
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge(f"{filename} exceeds {max_bytes // (1024 * 1024)} MB")
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                h.update(chunk)
                spool.write(chunk)
            digest = h.hexdigest()
            safe_name = Path(filename).name.replace("/", "_")[:200] or "upload.bin"
            rel = f"{container}/{digest[:2]}/{digest}/{safe_name}"
            spool.seek(0)
            self._upload(rel, spool.read())
        return StoredBlob(rel, digest, size, head)

    def put_bytes(self, data: bytes, filename: str, container: str = "derived") -> StoredBlob:
        import io
        return self.put_stream(io.BytesIO(data), filename, container)

    # ------------------------------------------------------------------ metadata
    def set_metadata(self, rel: str, metadata: dict[str, str]) -> None:
        meta = {f"x-ms-meta-{k}": str(v) for k, v in metadata.items()}
        self._upload(rel + ".metadata.json", json.dumps(meta, indent=2).encode(), "application/json")

    def get_metadata(self, rel: str) -> dict[str, str]:
        raw = self._download(rel + ".metadata.json")
        if raw is None:
            return {}
        return {k.removeprefix("x-ms-meta-"): v for k, v in json.loads(raw).items()}

    # ------------------------------------------------------------------ read
    def open(self, rel: str) -> BinaryIO:
        data = self._download(rel)
        if data is None:
            raise FileNotFoundError(rel)
        spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        spool.write(data)
        spool.seek(0)
        return spool  # type: ignore[return-value]

    def exists(self, rel: str) -> bool:
        return self._download(rel) is not None

    def delete(self, rel: str) -> None:
        self.http.request("DELETE", f"{self.url}/storage/v1/object/{self.bucket}",
                          json={"prefixes": [rel, rel + ".metadata.json"]})

    def append(self, rel: str, data: bytes) -> None:
        """DEMO ONLY: modify stored bytes in place (tamper simulation)."""
        current = self._download(rel)
        if current is None:
            raise FileNotFoundError(rel)
        self._upload(rel, current + data)


BlobStore = LocalBlobStore | SupabaseBlobStore
_store: BlobStore | None = None


def get_store() -> BlobStore:
    global _store
    if _store is None:
        backend = get_settings().storage_backend.lower()
        if backend == "supabase":
            _store = SupabaseBlobStore()
        elif backend == "local":
            _store = LocalBlobStore()
        else:
            raise RuntimeError(f"Unknown CIP_STORAGE_BACKEND {backend!r} (use 'local' or 'supabase')")
    return _store


MAGIC = {
    "application/pdf": [b"%PDF"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/jpeg": [b"\xff\xd8\xff"],
}


def sniff_content_type(head: bytes, filename: str) -> str | None:
    """Trust bytes, not the browser's Content-Type header."""
    if head.startswith(b"PK\x03\x04") and filename.lower().endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    for mime, sigs in MAGIC.items():
        if any(head.startswith(s) for s in sigs):
            return mime
    if filename.lower().endswith(".txt"):
        return "text/plain" if b"\x00" not in head else None
    return None
