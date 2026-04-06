from datetime import datetime

from pydantic import BaseModel


class UploadResponse(BaseModel):
    id: int
    file_name: str
    file_type: str
    file_size: int
    storage_path: str
    public_url: str
    linked_entity_type: str
    linked_entity_id: int
    created_at: datetime


class AttachmentResponse(UploadResponse):
    pass


class UploadLimitsResponse(BaseModel):
    allowed_types: list[str]
    max_upload_mb: int
