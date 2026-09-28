"""File storage for uploads.

The uploaded bytes are kept under ``settings.storage_path`` so the original can be downloaded.
Everything downstream reads ``document_sections``, never the file, so this module is the only
place that touches the file system for documents.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from app.config import get_settings

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_NAME = 120


def safe_filename(filename: str) -> str:
    """A file-system-safe basename that keeps the extension: no separators, no control chars."""
    name = Path(filename or "upload").name
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    stem = _UNSAFE.sub("_", stem).strip("._") or "upload"
    suffix = _UNSAFE.sub("", suffix)
    return f"{stem[:_MAX_NAME]}{suffix}"


def save_upload(
    data: bytes,
    filename: str,
    *,
    document_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
) -> str:
    """Write ``data`` under ``<storage_path>/<org_id>/<document_id>/<safe filename>``.

    Returns the absolute path as a string, which is what ``documents.storage_path`` stores.
    The document id is generated when not supplied so two uploads of the same filename never
    collide.
    """
    settings = get_settings()
    org = org_id or settings.default_org_id
    doc = document_id or uuid.uuid4()
    directory = Path(settings.storage_path) / str(org) / str(doc)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / safe_filename(filename)
    target.write_bytes(data)
    return str(target.resolve())


def storage_file(storage_path: str) -> Path:
    """The on-disk path for a stored ``storage_path``; relative values resolve under the
    configured storage root."""
    path = Path(storage_path)
    if not path.is_absolute():
        path = Path(get_settings().storage_path) / path
    return path


__all__ = ["safe_filename", "save_upload", "storage_file"]
