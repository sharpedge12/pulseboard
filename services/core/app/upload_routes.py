from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from shared.core.database import get_db
from shared.core.auth_helpers import get_current_user
from shared.core.config import settings
from shared.models.user import User
from shared.schemas.upload import UploadLimitsResponse, UploadResponse
from app.user_services import create_generic_upload

upload_router = APIRouter()

# Allowed values for linked_entity_type in upload requests
_ALLOWED_ENTITY_TYPES = {"draft", "thread", "post", "message", "avatars"}


@upload_router.get("/limits", response_model=UploadLimitsResponse)
def upload_limits() -> UploadLimitsResponse:
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
    if linked_entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid linked_entity_type: '{linked_entity_type}'. "
                f"Allowed: {', '.join(sorted(_ALLOWED_ENTITY_TYPES))}."
            ),
        )
    return create_generic_upload(
        db,
        current_user,
        file,
        linked_entity_type,
        linked_entity_id,
    )
