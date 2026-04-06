"""Attachment helpers — used by forum, chat, and user services."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.models.attachment import Attachment
from shared.models.user import User
from shared.schemas.upload import AttachmentResponse


def list_attachments(
    db: Session, linked_entity_type: str, linked_entity_ids: list[int]
) -> dict[int, list[AttachmentResponse]]:
    if not linked_entity_ids:
        return {}

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
    grouped: dict[int, list[AttachmentResponse]] = {
        entity_id: [] for entity_id in linked_entity_ids
    }
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
    if not attachment_ids:
        return

    attachments = (
        db.execute(
            select(Attachment).where(
                Attachment.id.in_(attachment_ids),
                Attachment.uploader_id == current_user.id,
                Attachment.linked_entity_type == "draft",
            )
        )
        .scalars()
        .all()
    )
    if len(attachments) != len(set(attachment_ids)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more attachments are invalid for this draft.",
        )

    for attachment in attachments:
        attachment.linked_entity_type = linked_entity_type
        attachment.linked_entity_id = linked_entity_id
