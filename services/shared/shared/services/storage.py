"""
File Upload Handling with Security Hardening
=============================================

INTERVIEW CONTEXT:
    File uploads are one of the most dangerous features to implement.
    Without proper validation, an attacker could:
    - Upload a ``.php`` or ``.jsp`` file and get Remote Code Execution
    - Upload a ``.html`` file with embedded JavaScript (stored XSS)
    - Upload a file named ``../../../etc/cron.d/backdoor`` (directory
      traversal — writing to arbitrary filesystem locations)
    - Upload a file with a spoofed MIME type (e.g., claim it's an image
      but it's actually a malicious PDF with JavaScript)
    - Upload a 10GB file to exhaust disk space (denial of service)

    This module implements **defense-in-depth** with 5 layers of
    validation, following OWASP File Upload guidelines.

SECURITY LAYERS:
    1. **MIME Type Whitelist** — only explicitly allowed types are accepted
       (deny-by-default, not deny-by-blacklist)
    2. **File Extension Whitelist** — only known-safe extensions are
       accepted, and they must match the declared MIME type
    3. **Magic Byte Verification** — the file's actual binary content is
       checked against known file signatures (prevents MIME spoofing)
    4. **File Size Limit** — enforced during streaming (not after), so
       large files are rejected mid-upload without consuming full disk
    5. **Filename Sanitization** — strips path components, replaces unsafe
       characters, prevents directory traversal attacks

USED BY:
    - **Core service** upload routes: ``save_upload_file()`` is called
      when a user uploads a file (avatar, thread attachment, etc.)
    - **Core service** admin routes: ``remove_upload_file()`` for cleanup
    - Both services use ``get_upload_root()`` for upload directory config

WHY IN THE SHARED LAYER?
    File storage logic is used by both the upload endpoint (Core) and
    the attachment display logic (Community).  The security validation
    functions are reusable and should be centralized so there's one
    place to update the allowlists.
"""

import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from shared.core.config import settings

# ---------------------------------------------------------------------------
# Allowed MIME types → category mapping
#
# INTERVIEW NOTE — WHITELIST vs BLACKLIST:
#   We use a whitelist (allowlist) of MIME types rather than a blacklist
#   (denylist).  A blacklist approach ("block .exe, .bat, .php") is
#   fragile because new dangerous file types emerge constantly.
#   A whitelist approach says "only these specific types are allowed"
#   and rejects everything else — much safer.
#
#   The category mapping (image/video/document) is used for organizing
#   uploads in the storage directory and for frontend display logic.
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
#
# INTERVIEW NOTE: Extensions are checked IN ADDITION TO MIME types.
# An attacker might set Content-Type: image/jpeg but name the file
# "malware.exe".  By checking both, we catch this mismatch.
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
# Magic-byte signatures for file-type verification
#
# INTERVIEW NOTE — WHAT ARE MAGIC BYTES?
#   Most binary file formats start with a specific byte sequence (the
#   "magic number" or "file signature") that identifies the format.
#   For example:
#     - JPEG files always start with bytes FF D8 FF
#     - PNG files always start with bytes 89 50 4E 47 0D 0A 1A 0A
#       (which spells "‰PNG\r\n\x1a\n")
#     - PDF files always start with "%PDF"
#
#   An attacker can easily change the Content-Type header or file
#   extension, but they CANNOT change the magic bytes without breaking
#   the file format.  So we read the first 32 bytes and verify they
#   match the expected signature for the claimed MIME type.
#
#   This prevents attacks like:
#     - Renaming malware.exe to photo.jpg and setting Content-Type
#       to image/jpeg — our magic byte check will detect the mismatch
#     - Uploading an HTML file with a .png extension — the magic bytes
#       won't match PNG's signature
#
#   Each entry maps a MIME type to a list of (offset, bytes) tuples.
#   The offset is where in the file to start checking (some formats
#   like WebP and MP4 have their signature a few bytes in).
# ---------------------------------------------------------------------------
_MAGIC_SIGNATURES: dict[str, list[tuple[int, bytes]]] = {
    "image/jpeg": [(0, b"\xff\xd8\xff")],  # JPEG: FF D8 FF
    "image/png": [(0, b"\x89PNG\r\n\x1a\n")],  # PNG: ‰PNG\r\n\x1a\n
    "image/webp": [(8, b"WEBP")],  # WebP: RIFF....WEBP (sig at offset 8)
    "image/gif": [(0, b"GIF87a"), (0, b"GIF89a")],  # GIF: either version
    "video/mp4": [(4, b"ftyp")],  # MP4: ....ftyp (sig at offset 4)
    "video/webm": [(0, b"\x1a\x45\xdf\xa3")],  # WebM: EBML header
    "application/pdf": [(0, b"%PDF")],  # PDF: %PDF
}

# ---------------------------------------------------------------------------
# Extension ↔ MIME mapping
#
# Used to verify that a file's extension is consistent with its declared
# MIME type.  For example, a .jpg file should have MIME type image/jpeg,
# not application/pdf.
#
# Types without magic-byte signatures (text/plain, .doc, .docx) are
# validated solely through this mapping + the MIME whitelist.
# ---------------------------------------------------------------------------
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
# 32 bytes is enough to cover all signatures in _MAGIC_SIGNATURES
# (the longest is PNG at 8 bytes starting at offset 0).
_MAGIC_READ_SIZE = 32

# Regex for sanitising original filenames — keep only safe characters.
# Everything that isn't alphanumeric, dot, underscore, or hyphen becomes
# an underscore.  This prevents directory traversal (../), null bytes,
# and other filesystem exploits.
_SAFE_FILENAME_RE = re.compile(r"[^a-zA-Z0-9._\-]")


def _sanitize_filename(raw: str) -> str:
    """Strip path components and unsafe characters from a user-supplied filename.

    INTERVIEW NOTE — DIRECTORY TRAVERSAL PREVENTION:
        The most critical step is ``Path(raw).name`` which extracts just
        the filename component, discarding any directory path.  Without
        this, an attacker could upload a file named:
            ``../../../etc/cron.d/backdoor``
        and potentially write to arbitrary filesystem locations.

        After extracting the basename, we also:
        - Replace all non-alphanumeric characters (except ``._-``) with
          underscores
        - Collapse repeated underscores (``a___b`` → ``a_b``)
        - Preserve the file extension even if the stem is entirely
          non-ASCII (e.g., ``日本語.jpg`` → ``upload.jpg``)

    Args:
        raw: The original filename from the upload (user-controlled,
            potentially malicious).

    Returns:
        A safe filename with only ``[a-zA-Z0-9._-]`` characters.
    """
    # Take only the basename — THIS IS THE CRITICAL SECURITY STEP
    # Prevents directory traversal (../../etc/passwd → passwd)
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

    # If the entire stem was unsafe characters, use a generic name
    if not stem:
        stem = "upload"

    return stem + ext


def _verify_magic_bytes(
    file_header: bytes,
    claimed_mime: str,
) -> bool:
    """Check file header against known magic-byte signatures.

    INTERVIEW NOTE — WHY THIS MATTERS:
        A user can trivially fake the ``Content-Type`` header in their
        HTTP request.  Magic byte verification checks the ACTUAL file
        content, not the claimed type.  This is the difference between
        trusting user input (bad) and verifying it (good).

    Args:
        file_header: The first ``_MAGIC_READ_SIZE`` bytes of the file.
        claimed_mime: The MIME type declared by the client.

    Returns:
        ``True`` if:
        - The claimed MIME type has a matching magic signature in the
          file header, OR
        - We don't have a signature for that type (e.g. ``text/plain``,
          ``.doc``, ``.docx``).  These are still validated by the MIME
          whitelist and extension check.

        ``False`` if the file's magic bytes don't match the expected
        signature for the claimed MIME type.
    """
    signatures = _MAGIC_SIGNATURES.get(claimed_mime)
    if signatures is None:
        # No magic-byte check available for this MIME — allow (validated
        # by ALLOWED_CONTENT_TYPES + extension already).
        return True

    # Check if ANY of the known signatures match at their expected offset
    return any(
        len(file_header) >= offset + len(sig)
        and file_header[offset : offset + len(sig)] == sig
        for offset, sig in signatures
    )


def _validate_extension_matches_mime(extension: str, content_type: str) -> bool:
    """Ensure the file extension is consistent with the claimed MIME type.

    Prevents mismatches like a ``.exe`` file claiming to be ``image/jpeg``.

    Args:
        extension: The lowercase file extension including the dot
            (e.g. ``".jpg"``).
        content_type: The MIME type declared by the client.

    Returns:
        ``True`` if the extension is mapped to the given MIME type in
        ``_EXTENSION_MIME_MAP``.
    """
    allowed_mimes = _EXTENSION_MIME_MAP.get(extension)
    if allowed_mimes is None:
        return False
    return content_type in allowed_mimes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_upload_root() -> Path:
    """Return the root upload directory, creating it if needed.

    The path comes from ``settings.upload_dir`` (configured via
    environment variable).  Directory is created with mode 0o755
    (owner rwx, group/others rx).

    Returns:
        A ``Path`` object pointing to the upload root directory.
    """
    upload_dir = Path(settings.upload_dir)
    os.makedirs(upload_dir, mode=0o755, exist_ok=True)
    return upload_dir


def save_upload_file(upload_file: UploadFile, folder: str) -> dict[str, str | int]:
    """Validate, store, and return metadata for an uploaded file.

    INTERVIEW NOTE — THE 5 SECURITY LAYERS:
        This function implements the full defense-in-depth upload
        validation pipeline:

        1. **MIME type whitelist**: Is the declared Content-Type in our
           allowlist?  (Rejects ``application/x-executable``, etc.)
        2. **Extension whitelist + matching**: Is the file extension
           allowed, AND does it match the declared MIME type?  (Catches
           ``malware.exe`` with Content-Type: image/jpeg)
        3. **Magic byte verification**: Do the file's first 32 bytes
           match the expected binary signature?  (Catches renamed files)
        4. **Streaming size check**: Is the file within the size limit?
           Checked DURING streaming, not after — so a 10GB upload is
           rejected after reading ``max_size`` bytes, not after writing
           the full 10GB to disk.
        5. **Filename sanitization**: Is the filename safe for the
           filesystem?  (Prevents ``../../etc/passwd`` directory
           traversal)

        The stored filename is a UUID (``uuid4().hex``), so even if
        sanitization somehow failed, the filename would be a random
        hex string — not the user's original name.

    Args:
        upload_file: The FastAPI ``UploadFile`` object from the request.
        folder: Subdirectory within the upload root to store the file
            (e.g. ``"avatars"``, ``"attachments"``).

    Returns:
        A dict with metadata about the stored file:
        - ``file_name``: The sanitized original filename
        - ``file_type``: The category (``"image"``, ``"video"``,
          ``"document"``)
        - ``file_size``: Size in bytes
        - ``storage_path``: Relative path from upload root (e.g.
          ``"avatars/a1b2c3d4.jpg"``)
        - ``public_url``: URL path for the frontend (e.g.
          ``"/uploads/avatars/a1b2c3d4.jpg"``)

    Raises:
        HTTPException(400): If any validation step fails.

    Side effects:
        - Reads the file content from the upload stream
        - Writes the file to disk
        - Creates the target directory if it doesn't exist
    """

    # --- Layer 1: MIME type whitelist check ---
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

    # --- Layer 2: Extension whitelist + MIME match ---
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

    # Cross-check: extension must be compatible with the declared MIME type
    if not _validate_extension_matches_mime(extension, content_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File extension does not match the declared content type.",
        )

    # --- Layer 3: Magic-byte verification ---
    # Read the first 32 bytes to check the file's binary signature
    file_header = upload_file.file.read(_MAGIC_READ_SIZE)
    upload_file.file.seek(0)  # Reset read position for the actual write

    if not _verify_magic_bytes(file_header, content_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File content does not match the declared file type.",
        )

    # --- Layers 4 & 5: Streaming write with size check + UUID filename ---
    target_dir = get_upload_root() / folder
    os.makedirs(target_dir, mode=0o755, exist_ok=True)

    # Use UUID for the stored filename — prevents collisions and eliminates
    # any residual filename-based attacks even if sanitization were bypassed
    stored_name = f"{uuid4().hex}{extension}"
    target_path = target_dir / stored_name
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    # Stream the file in 1MB chunks, checking size as we go
    # This is more memory-efficient than reading the entire file at once
    # and prevents oversized files from consuming all available disk
    size = 0
    with target_path.open("wb") as output_stream:
        while True:
            chunk = upload_file.file.read(1024 * 1024)  # 1MB chunks
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                # File is too large — clean up the partial file and reject
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

    # Reset the upload file read position (for any downstream processing)
    upload_file.file.seek(0)

    # Build the relative storage path and public URL
    relative_path = target_path.relative_to(get_upload_root())
    return {
        "file_name": safe_name,
        "file_type": category,
        "file_size": size,
        "storage_path": str(relative_path).replace("\\", "/"),
        "public_url": f"/uploads/{str(relative_path).replace(chr(92), '/')}",
    }


def remove_upload_file(storage_path: str) -> None:
    """Delete a previously uploaded file from disk.

    Used during content deletion or moderation actions to clean up
    orphaned files.

    Args:
        storage_path: The relative path from the upload root (as
            returned by ``save_upload_file()``'s ``storage_path`` key).

    Side effects:
        Deletes the file from disk if it exists.  No error if the file
        is already gone (idempotent).
    """
    target_path = get_upload_root() / storage_path
    if target_path.exists():
        target_path.unlink()


def clear_uploads() -> None:
    """Delete ALL uploaded files — used in testing and development.

    WARNING: This removes the entire upload directory tree and recreates
    it empty.  Never call this in production.

    Side effects:
        Recursively deletes the upload root directory and recreates it.
    """
    upload_root = get_upload_root()
    if upload_root.exists():
        shutil.rmtree(upload_root)
        upload_root.mkdir(parents=True, exist_ok=True)
