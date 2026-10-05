"""Collection and document operations shared by the API and the CLI."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .errors import DocSageError, NotFound
from .ingest.estimate import estimate_file
from .ingest.parsers import SUPPORTED, kind_for
from .providers.factory import default_embedding_spec
from .store import get_db, get_vectors

log = logging.getLogger(__name__)

SAMPLE_NAME = "Sample: Multimodal demo"


def safe_filename(name: str) -> str:
    name = Path(name).name
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", stem).strip(" .") or "file"
    return f"{stem[:120]}{suffix}"


def create_collection(name: str, description: str = "", settings: Settings | None = None) -> dict[str, Any]:
    if not name.strip():
        raise DocSageError("Give the collection a name.")
    spec = default_embedding_spec(settings or get_settings())
    return get_db().create_collection(name, description, spec.as_dict())


def ensure_default_collection() -> dict[str, Any]:
    db = get_db()
    existing = db.list_collections()
    return existing[0] if existing else create_collection("My documents", "Default collection")


def stage_file(cid: str, filename: str, data: bytes, settings: Settings | None = None) -> dict[str, Any]:
    """Store an upload, de-duplicate by content hash and attach a cost estimate."""
    settings = settings or get_settings()
    db = get_db()
    db.get_collection(cid)
    name = safe_filename(filename)
    kind = kind_for(name)
    if len(data) == 0:
        raise DocSageError(f"'{name}' is empty.")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise DocSageError(f"'{name}' is larger than the {settings.max_upload_mb} MB upload limit.")
    digest = hashlib.sha256(data).hexdigest()
    duplicate = db.find_document_by_hash(cid, digest)
    if duplicate and duplicate["status"] not in ("failed", "cancelled"):
        return {**duplicate, "duplicate": True}
    folder = settings.data_dir / "uploads" / cid
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest[:12]}_{name}"
    path.write_bytes(data)
    try:
        estimate = estimate_file(path, settings)
    except DocSageError:
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise DocSageError(f"'{name}' could not be read: {exc}") from exc
    if duplicate:  # a failed earlier attempt: reuse the row
        db.update_document(duplicate["id"], status="staged", error=None, estimate=estimate, path=str(path))
        return {**db.get_document(duplicate["id"]), "duplicate": False}
    doc = db.create_document(
        collection_id=cid,
        filename=name,
        path=str(path),
        sha256=digest,
        size_bytes=len(data),
        kind=kind,
        estimate=estimate,
    )
    return {**doc, "duplicate": False}


def delete_document(doc_id: str) -> None:
    settings = get_settings()
    db = get_db()
    doc = db.get_document(doc_id)
    if doc["status"] in ("queued", "processing"):
        raise DocSageError("This document is being processed. Cancel the job first.")
    get_vectors().delete_document(doc["collection_id"], doc_id)
    db.delete_document(doc_id)
    db.bump_version(doc["collection_id"])
    shutil.rmtree(settings.data_dir / "assets" / doc["collection_id"] / doc_id, ignore_errors=True)
    path = Path(doc["path"])
    still_used = db.scalar("SELECT 1 FROM documents WHERE path=?", (str(path),))
    if not still_used and settings.data_dir.resolve() in path.resolve().parents:
        path.unlink(missing_ok=True)


def delete_collection(cid: str) -> None:
    settings = get_settings()
    db = get_db()
    db.get_collection(cid)
    if db.list_jobs(cid, active=True):
        raise DocSageError("This collection has running jobs. Cancel them first.")
    get_vectors().drop(cid)
    db.delete_collection(cid)
    for sub in ("assets", "uploads"):
        shutil.rmtree(settings.data_dir / sub / cid, ignore_errors=True)


def load_samples() -> dict[str, Any]:
    from .jobs import submit_ingest
    from .samples import GOLDEN, write_samples

    settings = get_settings()
    db = get_db()
    existing = next((c for c in db.list_collections() if c["name"] == SAMPLE_NAME), None)
    if existing:
        return existing
    collection = create_collection(
        SAMPLE_NAME,
        "Fictional annual report, field guide, launch deck and runbook "
        "with tables and charts. Includes a golden question set.",
    )
    tmp = settings.data_dir / "tmp" / "samples"
    paths = write_samples(tmp)
    for path in paths:
        doc = stage_file(collection["id"], path.name, path.read_bytes(), settings)
        submit_ingest(doc["id"])
    for item in GOLDEN:
        db.add_eval_item(collection["id"], **item, source="sample")
    shutil.rmtree(tmp, ignore_errors=True)
    return db.get_collection(collection["id"])


def iter_supported(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED else []
    return sorted(
        p
        for p in path.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED
        and not any(part.startswith(".") for part in p.relative_to(path).parts)
    )


def find_collection(name_or_id: str) -> dict[str, Any]:
    db = get_db()
    try:
        return db.get_collection(name_or_id)
    except NotFound:
        for c in db.list_collections():
            if c["name"].lower() == name_or_id.lower():
                return c
        raise
