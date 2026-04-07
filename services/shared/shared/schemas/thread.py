from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class ThreadCreateRequest(BaseModel):
    category_id: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)
    tag_names: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        return sanitize_text(v)

    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)

    @field_validator("tag_names")
    @classmethod
    def clean_tags(cls, v: list[str]) -> list[str]:
        cleaned = []
        for tag in v:
            tag = tag.strip().lower()
            if tag and len(tag) <= 60:
                cleaned.append(sanitize_text(tag))
        return cleaned


class ThreadUpdateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        return sanitize_text(v)

    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)


class ThreadAuthorResponse(BaseModel):
    id: int
    username: str
    role: str
    avatar_url: str | None = None


class ThreadCategoryResponse(BaseModel):
    id: int
    title: str
    slug: str


from shared.schemas.post import PostResponse
from shared.schemas.upload import AttachmentResponse
from shared.schemas.vote import ReactionCountResponse
from shared.schemas.tag import TagResponse


class ThreadListItemResponse(BaseModel):
    id: int
    title: str
    body: str
    is_locked: bool
    is_pinned: bool
    created_at: datetime
    updated_at: datetime
    reply_count: int
    vote_score: int = 0
    user_vote: int = 0
    reactions: list[ReactionCountResponse] = []
    author: ThreadAuthorResponse
    category: ThreadCategoryResponse
    attachments: list[AttachmentResponse] = []
    tags: list[TagResponse] = []


class ThreadDetailResponse(ThreadListItemResponse):
    posts: list[PostResponse]


class PaginatedThreadsResponse(BaseModel):
    items: list[ThreadListItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
