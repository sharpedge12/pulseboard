"""
Attachment Linking Service — Two-Phase Upload Lifecycle
========================================================

INTERVIEW CONTEXT:
    File uploads in PulseBoard follow a **two-phase pattern**:

    Phase 1 — DRAFT:
        When a user selects a file (before submitting their post/thread),
        the file is uploaded immediately via ``POST /api/v1/uploads/``.
        The resulting ``Attachment`` row has ``linked_entity_type="draft"``
        and no ``linked_entity_id``.  This gives the user instant
        feedback (preview, progress bar) without waiting for form
        submission.

    Phase 2 — LINKED:
        When the user submits their post/thread/message, the route
        handler calls ``assign_attachments_to_entity()`` to change the
        attachment's ``linked_entity_type`` from ``"draft"`` to the
        actual entity type (``"thread"``, ``"post"``, ``"message"``)
        and set the ``linked_entity_id``.

    This two-phase approach is common in modern web apps because:
    - Users can preview uploads before submitting
    - Large file uploads don't block form submission
    - If the user abandons the form, draft attachments can be cleaned up
      by a periodic job (not yet implemented)

USED BY:
    - **Core service** upload routes: ``save_upload_file()`` creates the
      initial draft attachment.
    - **Community service** forum routes: ``assign_attachments_to_entity()``
      links drafts to threads/posts after creation.
    - **Community service** chat routes: same for chat messages.
    - **All services** that display content: ``list_attachments()`` loads
      attachments for rendering (thread detail page, chat messages, etc.).

WHY IN THE SHARED LAYER?
    Uploads are created by Core (which handles the file storage endpoint)
    but consumed by Community (which displays threads/posts/chat with
    their attachments).  Both services need ``list_attachments()`` and
    ``assign_attachments_to_entity()``.

SECURITY NOTE:
    ``assign_attachments_to_entity()`` verifies that:
    1. Each attachment belongs to the current user (``uploader_id`` check)
    2. Each attachment is still in "draft" state (prevents re-linking an
       already-linked attachment to a different entity)
    If either check fails, a 400 error is raised.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.models.attachment import Attachment
from shared.models.user import User
from shared.schemas.upload import AttachmentResponse


def list_attachments(
    db: Session, linked_entity_type: str, linked_entity_ids: list[int]
) -> dict[int, list[AttachmentResponse]]:
    """Load attachments for a batch of entities, grouped by entity ID.

    INTERVIEW NOTE — BATCH LOADING (AVOIDING N+1):
        Instead of loading attachments for each thread/post individually
        (N+1 query problem), we load all attachments for ALL entity IDs
        in a single query and group them in Python.  This is a standard
        optimization for list views.

        Example: displaying 20 threads on the homepage:
        - BAD:  20 separate queries (one per thread)
        - GOOD: 1 query with ``WHERE linked_entity_id IN (1,2,...,20)``

    Args:
        db: Active SQLAlchemy session.
        linked_entity_type: The type of entity (``"thread"``,
            ``"post"``, ``"message"``).
        linked_entity_ids: List of entity IDs to load attachments for.

    Returns:
        A dict mapping each entity ID to its list of
        ``AttachmentResponse`` objects.  Entity IDs with no attachments
        will have an empty list (not be absent from the dict).

    Side effects:
        Read-only — queries the ``attachments`` table.
    """
    if not linked_entity_ids:
        return {}

    # Single query to fetch all attachments for all requested entities
    attachments = (
        db.execute(
            select(Attachment).where(
                Attachment.linked_entity_type == linked_entity_type,
                Attachment.linked_entity_id.in_(linked_entity_ids),
            )
        )
        .scalars()
        .all()
    )

    # Pre-populate the result dict with empty lists for every requested ID
    # so callers don't need to handle missing keys
    grouped: dict[int, list[AttachmentResponse]] = {
        entity_id: [] for entity_id in linked_entity_ids
    }

    # Group attachments by their linked entity ID
    for attachment in attachments:
        grouped.setdefault(attachment.linked_entity_id, []).append(
            AttachmentResponse(
                id=attachment.id,
                file_name=attachment.file_name,
                file_type=attachment.file_type,
                file_size=attachment.file_size,
                storage_path=attachment.storage_path,
                public_url=f"/uploads/{attachment.storage_path}",
                linked_entity_type=attachment.linked_entity_type,
                linked_entity_id=attachment.linked_entity_id,
                created_at=attachment.created_at,
            )
        )
    return grouped


def assign_attachments_to_entity(
    db: Session,
    current_user: User,
    attachment_ids: list[int],
    linked_entity_type: str,
    linked_entity_id: int,
) -> None:
    """Link draft attachments to a newly created entity (thread/post/message).

    INTERVIEW NOTE — OWNERSHIP + STATE VALIDATION:
        This function enforces two critical security checks:

        1. **Ownership**: ``Attachment.uploader_id == current_user.id``
           Prevents user A from linking user B's uploads to their own post.

        2. **Draft state**: ``Attachment.linked_entity_type == "draft"``
           Prevents re-linking an already-linked attachment.  Without this
           check, a user could link the same file to multiple posts, or
           worse, steal an attachment from someone else's post by
           re-linking it.

        If ANY of the requested attachment IDs fail these checks, the
        entire operation fails with a 400 error (no partial linking).

    Args:
        db: Active SQLAlchemy session.  Changes are made but NOT
            committed — the caller commits after the primary entity
            (thread/post/message) is also created.
        current_user: The authenticated user making the request.
        attachment_ids: List of attachment IDs to link (from the
            ``attachment_ids`` field in the request body).
        linked_entity_type: The target entity type (``"thread"``,
            ``"post"``, ``"message"``).
        linked_entity_id: The target entity's primary key.

    Raises:
        HTTPException(400): If any attachment ID is invalid, doesn't
            belong to the current user, or is not in draft state.

    Side effects:
        Updates the ``linked_entity_type`` and ``linked_entity_id``
        columns of matching Attachment rows (but does NOT commit).
    """
    if not attachment_ids:
        return

    # Fetch all requested attachments that match our security criteria:
    # - Must belong to the current user (ownership check)
    # - Must be in "draft" state (prevents re-linking)
    attachments = (
        db.execute(
            select(Attachment).where(
                Attachment.id.in_(attachment_ids),
                Attachment.uploader_id == current_user.id,  # Ownership check
                Attachment.linked_entity_type == "draft",  # State check
            )
        )
        .scalars()
        .all()
    )

    # If any attachment didn't pass the checks, the counts won't match.
    # We reject the entire request rather than partially linking.
    if len(attachments) != len(set(attachment_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more attachments are invalid for this draft.",
        )

    # Transition each attachment from "draft" to the target entity
    for attachment in attachments:
        attachment.linked_entity_type = linked_entity_type
        attachment.linked_entity_id = linked_entity_id
