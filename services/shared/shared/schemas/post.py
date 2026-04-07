from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from shared.schemas.upload import AttachmentResponse
from shared.schemas.vote import ReactionCountResponse
from shared.services.sanitize import sanitize_text


class PostCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    parent_post_id: int | None = None
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)


class PostUpdateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)


class PostAuthorResponse(BaseModel):
    id: int
    username: str
    role: str
    avatar_url: str | None = None


class PostResponse(BaseModel):
    id: int
    thread_id: int
    parent_post_id: int | None
    body: str
    created_at: datetime
    updated_at: datetime
    vote_score: int = 0
    user_vote: int = 0
    reactions: list[ReactionCountResponse] = []
    author: PostAuthorResponse
    attachments: list[AttachmentResponse] = []
    replies: list["PostResponse"] = []


PostResponse.model_rebuild()
