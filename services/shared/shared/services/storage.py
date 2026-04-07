"""File storage helpers — used by user and upload services."""

import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from shared.core.config import settings

# ---------------------------------------------------------------------------
# Allowed MIME types → category mapping
# ---------------------------------------------------------------------------
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/webp": "image",
    "image/gif": "image",
    "video/mp4": "video",
    "video/webm": "video",
    "application/pdf": "document",
    "text/plain": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
}

# ---------------------------------------------------------------------------
# Allowed file extensions (lowercase, with dot)
# ---------------------------------------------------------------------------
ALLOWED_EXTENSIONS: set[str] = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".mp4",
    ".webm",
    ".pdf",
    ".txt",
    ".doc",
    ".docx",
}

# ---------------------------------------------------------------------------
# Magic-byte signatures for file-type verification.
# Each entry maps a MIME type to a list of (offset, bytes) tuples.  At least
# one signature must match for that MIME type to be accepted.
# ---------------------------------------------------------------------------
_MAGIC_SIGNATURES: dict[str, list[tuple[int, bytes]]] = {
    "image/jpeg": [(0, b"\xff\xd8\xff")],
    "image/png": [(0, b"\x89PNG\r\n\x1a\n")],
    "image/webp": [(8, b"WEBP")],  # RIFF....WEBP
    "image/gif": [(0, b"GIF87a"), (0, b"GIF89a")],
    "video/mp4": [(4, b"ftyp")],  # ....ftyp
    "video/webm": [(0, b"\x1a\x45\xdf\xa3")],  # EBML header
    "application/pdf": [(0, b"%PDF")],
}

# Extension ↔ MIME mapping for types that aren't verified by magic bytes
_EXTENSION_MIME_MAP: dict[str, set[str]] = {
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
    ".webp": {"image/webp"},
    ".gif": {"image/gif"},
    ".mp4": {"video/mp4"},
    ".webm": {"video/webm"},
    ".pdf": {"application/pdf"},
    ".txt": {"text/plain"},
    ".doc": {"application/msword"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
}

# Minimum number of bytes we need to read for magic-byte verification.
_MAGIC_READ_SIZE = 32

# Regex for sanitising original filenames — keep only safe characters.
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-]")


def _sanitize_filename(raw: str) -> str:
    """Strip path components and unsafe characters from a user-supplied filename.

    Preserves the file extension even if all stem characters are non-ASCII.
    """
    # Take only the basename (prevent directory traversal)
    name = Path(raw).name

    # Separate stem and extension before sanitising — we must keep the
    # extension intact even if the stem is entirely non-ASCII.
    p = Path(name)
    stem = p.stem
    ext = p.suffix  # e.g. ".jpg"

    # Handle dotfiles like ".jpg" where Python sees stem=".jpg", suffix=""
    if not ext and stem.startswith("."):
        ext = stem
        stem = ""

    # Replace unsafe characters with underscores in the stem
    stem = _SAFE_FILENAME_RE.sub("_", stem)
    # Collapse repeated underscores and strip leading/trailing underscores
    stem = re.sub(r"_+", "_", stem).strip("_")

    if not stem:
        stem = "upload"

    return stem + ext


def _verify_magic_bytes(
    file_header: bytes,
    claimed_mime: str,
) -> bool:
    """Check file header against known magic-byte signatures.

    Returns True if the claimed MIME type has a matching magic signature
    or if we don't have a signature for that type (text/plain, .doc, .docx).
    """
    signatures = _MAGIC_SIGNATURES.get(claimed_mime)
    if signatures is None:
        # No magic-byte check available for this MIME — allow (validated
        # by ALLOWED_CONTENT_TYPES + extension already).
        return True
    return any(
        len(file_header) >= offset + len(sig)
        and file_header[offset : offset + len(sig)] == sig
        for offset, sig in signatures
    )


def _validate_extension_matches_mime(extension: str, content_type: str) -> bool:
    """Ensure the file extension is consistent with the claimed MIME type."""
    allowed_mimes = _EXTENSION_MIME_MAP.get(extension)
    if allowed_mimes is None:
        return False
    return content_type in allowed_mimes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_upload_root() -> Path:
    upload_dir = Path(settings.upload_dir)
    os.makedirs(upload_dir, mode=0o755, exist_ok=True)
    return upload_dir


def save_upload_file(upload_file: UploadFile, folder: str) -> dict[str, str | int]:
    """Validate, store and return metadata for an uploaded file.

    Validations performed:
    1. MIME type is in the allowlist (``ALLOWED_CONTENT_TYPES``).
    2. File extension is in the allowlist and matches the MIME type.
    3. Magic bytes match the claimed MIME type (where signatures exist).
    4. File size does not exceed ``settings.max_upload_size_mb``.
    5. Original filename is sanitised (no path traversal, safe chars only).
    """

    # --- 1. MIME type check ---
    content_type = upload_file.content_type or ""
    category = ALLOWED_CONTENT_TYPES.get(content_type)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file type: '{content_type}'. "
                "Allowed: images (JPEG, PNG, WebP, GIF), "
                "videos (MP4, WebM), documents (PDF, TXT, DOC, DOCX)."
            ),
        )

    # --- 2. Extension check ---
    raw_name = upload_file.filename or "upload.bin"
    safe_name = _sanitize_filename(raw_name)
    extension = Path(safe_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"File extension '{extension}' is not allowed. "
                f"Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
            ),
        )

    if not _validate_extension_matches_mime(extension, content_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File extension does not match the declared content type.",
        )

    # --- 3. Magic-byte check ---
    file_header = upload_file.file.read(_MAGIC_READ_SIZE)
    upload_file.file.seek(0)

    if not _verify_magic_bytes(file_header, content_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match the declared file type.",
        )

    # --- 4. Write with size check ---
    target_dir = get_upload_root() / folder
    os.makedirs(target_dir, mode=0o755, exist_ok=True)

    stored_name = f"{uuid4().hex}{extension}"
    target_path = target_dir / stored_name
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    size = 0
    with target_path.open("wb") as output_stream:
        while True:
            chunk = upload_file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                output_stream.close()
                target_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"File exceeds the maximum upload size "
                        f"of {settings.max_upload_size_mb} MB."
                    ),
                )
            output_stream.write(chunk)

    upload_file.file.seek(0)

    relative_path = target_path.relative_to(get_upload_root())
    return {
        "file_name": safe_name,
        "file_type": category,
        "file_size": size,
        "storage_path": str(relative_path).replace("\\", "/"),
        "public_url": f"/uploads/{str(relative_path).replace(chr(92), '/')}",
    }


def remove_upload_file(storage_path: str) -> None:
    target_path = get_upload_root() / storage_path
    if target_path.exists():
        target_path.unlink()


def clear_uploads() -> None:
    upload_root = get_upload_root()
    if upload_root.exists():
        shutil.rmtree(upload_root)
        upload_root.mkdir(parents=True, exist_ok=True)
