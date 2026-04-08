"""
Upload Routes — Core Service
==============================

This module defines the HTTP endpoints for generic file uploads.  It handles
uploading files that are attached to various entities (threads, posts, chat
messages, drafts).  Avatar uploads are handled separately in ``user_routes.py``.

Endpoints:

    - ``GET  /limits``  — Return the allowed file types and max upload size.
    - ``POST /``        — Upload a file and link it to an entity.

Key interview concepts:

  - **Entity type whitelist**: The ``_ALLOWED_ENTITY_TYPES`` set is a
    security measure that restricts which entity types can be used in upload
    requests.  Without this, an attacker could upload files linked to
    arbitrary entity types, potentially exploiting unexpected code paths
    in the storage layer (e.g., creating directories with attacker-controlled
    names, or storing files in unintended locations).

  - **Multipart form data**: File uploads use ``Content-Type: multipart/form-data``
    (not JSON).  FastAPI's ``File(...)`` and ``Form(...)`` dependencies parse
    the multipart body.  ``File`` extracts the binary file data, while ``Form``
    extracts the text fields (``linked_entity_type`` and ``linked_entity_id``).

  - **Validation layers**: File uploads are validated at multiple levels:
      1. **Route layer** (this module): Entity type whitelist check.
      2. **Storage layer** (``shared/services/storage.py``): MIME type validation,
         magic-byte verification, file extension whitelist, filename sanitization,
         and file size limit enforcement.
    This defense-in-depth approach ensures that even if one layer is bypassed,
    the others still protect the system.

  - **Configuration-driven limits**: The max upload size is read from
    ``settings.max_upload_size_mb`` (environment variable), making it easy
    to adjust without code changes.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user
from shared.core.config import settings
from shared.models.user import User
from shared.schemas.upload import UploadLimitsResponse, UploadResponse
from app.user_services import create_generic_upload

upload_router = APIRouter()

# ---------------------------------------------------------------------------
# Entity type whitelist
# ---------------------------------------------------------------------------
# This set controls which ``linked_entity_type`` values are accepted in
# upload requests.  Any value not in this set is rejected with HTTP 400.
#
# Allowed types:
#   - "draft"    — File attached to an unsaved draft (before thread/post creation).
#   - "thread"   — File attached to a thread's body.
#   - "post"     — File attached to a reply/comment.
#   - "message"  — File attached to a chat message.
#   - "avatars"  — User avatar image (though avatar uploads typically use
#                  the dedicated ``POST /users/me/avatar`` endpoint).
# ---------------------------------------------------------------------------
_ALLOWED_ENTITY_TYPES = {"draft", "thread", "post", "message", "avatars"}


@upload_router.get("/limits", response_model=UploadLimitsResponse)
def upload_limits() -> UploadLimitsResponse:
    """Return the file upload constraints for the frontend to enforce client-side.

    The frontend's ``uploadUtils.js`` calls this endpoint on app init to learn
    the allowed file categories and maximum size, so it can show validation
    errors *before* the user attempts an upload (better UX than waiting for
    a server rejection).

    No authentication is required — these are public configuration values.

    Returns:
        UploadLimitsResponse: ``allowed_types`` (categories) and ``max_upload_mb``.
    """
    return UploadLimitsResponse(
        allowed_types=["image", "video", "document"],
        max_upload_mb=settings.max_upload_size_mb,
    )


@upload_router.post("", response_model=UploadResponse)
def create_upload(
    linked_entity_type: str = Form(...),
    linked_entity_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadResponse:
    """Upload a file and link it to a content entity (thread, post, message, etc.).

    This endpoint accepts a multipart form with three fields:
      - ``linked_entity_type`` (text): What kind of content the file belongs to.
      - ``linked_entity_id`` (integer): The database ID of that content.
      - ``file`` (binary): The actual file data.

    The upload flow:
      1. Validate ``linked_entity_type`` against the whitelist (this function).
      2. Delegate to ``create_generic_upload`` which calls ``save_upload_file``
         to validate and persist the file, then creates an ``Attachment`` DB record.

    Args:
        linked_entity_type: The type of entity to link the file to.
            Must be one of: ``draft``, ``thread``, ``post``, ``message``, ``avatars``.
        linked_entity_id: The database ID of the entity to link to.
        file: The uploaded file (multipart form data).
        db: SQLAlchemy session (injected by DI).
        current_user: The authenticated user performing the upload (from JWT).

    Returns:
        UploadResponse: Metadata about the saved file (id, name, type, size,
            public URL, linked entity info, creation timestamp).

    Raises:
        HTTPException 400: If ``linked_entity_type`` is not in the whitelist,
            or if the file fails validation (wrong MIME type, too large, etc.).
        HTTPException 401: If the user is not authenticated.
    """
    # Whitelist check — reject unknown entity types before touching the filesystem.
    if linked_entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid linked_entity_type: '{linked_entity_type}'. "
                f"Allowed: {', '.join(sorted(_ALLOWED_ENTITY_TYPES))}."
            ),
        )

    # Delegate to the service layer for file validation, storage, and DB record creation.
    return create_generic_upload(
        db,
        current_user,
        file,
        linked_entity_type,
        linked_entity_id,
    )
