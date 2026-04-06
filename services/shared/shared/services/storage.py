"""File storage helpers — used by user and upload services."""

import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from shared.core.config import settings

ALLOWED_CONTENT_TYPES = {
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


def get_upload_root() -> Path:
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def save_upload_file(upload_file: UploadFile, folder: str) -> dict[str, str | int]:
    content_type = upload_file.content_type or ""
    category = ALLOWED_CONTENT_TYPES.get(content_type)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type.",
        )

    safe_name = upload_file.filename or "upload.bin"
    extension = Path(safe_name).suffix.lower()
    target_dir = get_upload_root() / folder
    target_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid4().hex}{extension}"
    target_path = target_dir / stored_name

    size = 0
    with target_path.open("wb") as output_stream:
        while True:
            chunk = upload_file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > settings.max_upload_size_mb * 1024 * 1024:
                output_stream.close()
                target_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="File exceeds the maximum upload size.",
                )
            output_stream.write(chunk)

    upload_file.file.seek(0)

    relative_path = target_path.relative_to(get_upload_root())
    return {
        "file_name": safe_name,
        "file_type": category,
        "file_size": size,
        "storage_path": str(relative_path).replace("\\", "/"),
        "public_url": f"/uploads/{str(relative_path).replace('\\', '/')}",
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
