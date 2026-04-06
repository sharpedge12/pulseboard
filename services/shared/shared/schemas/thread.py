from datetime import datetime

from pydantic import BaseModel, Field

from shared.schemas.post import PostResponse
from shared.schemas.upload import AttachmentResponse
from shared.schemas.vote import ReactionCountResponse
from shared.schemas.tag import TagResponse


class ThreadCreateRequest(BaseModel):
    category_id: int
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[int] = Field(default_factory=list)
    tag_names: list[str] = Field(default_factory=list)


class ThreadUpdateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=1, max_length=10000)


class ThreadAuthorResponse(BaseModel):
    id: int
    username: str
    role: str
    avatar_url: str | None = None


class ThreadCategoryResponse(BaseModel):
    id: int
    title: str
    slug: str


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
