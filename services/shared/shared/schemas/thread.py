"""
Forum Thread Schemas
====================

This module defines Pydantic models for creating, updating, listing, and
viewing forum threads (discussions).

**Interview Concept: Input validation as a security boundary**

Threads are the primary content type in the forum.  Their ``title`` and
``body`` fields accept user-generated text that gets stored in the database
and displayed to every user who views the thread.  This makes them a prime
target for **Stored XSS** (Cross-Site Scripting) attacks — where an
attacker stores malicious JavaScript in the database that executes in
other users' browsers.

Every text field in request schemas passes through ``sanitize_text()`` which
strips ``<script>`` tags, ``javascript:`` URIs, and ``onerror=`` event
handlers.  This is a backend defense-in-depth layer — React also escapes
output on the frontend, but we sanitize on input to protect against
direct API usage (e.g., via curl or Postman).

**Interview Concept: List vs Detail response shapes**

- ``ThreadListItemResponse`` — Used in the thread feed.  Contains metadata
  (title, vote score, reply count, tags) but NOT the full list of replies.
- ``ThreadDetailResponse`` — Used on the single-thread page.  Extends the
  list item and includes all ``posts`` (replies).  This avoids loading
  hundreds of replies for every thread in a paginated feed.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class ThreadCreateRequest(BaseModel):
    """
    Schema for creating a new thread (POST /api/v1/threads).

    Fields:
    - ``category_id``: Which forum category this thread belongs to.
      ``ge=1`` ensures we never accept 0 or negative IDs (which would
      be meaningless and could indicate a bug or manipulation attempt).
    - ``title``: 3-255 chars.  The min prevents empty/trivial titles;
      the max prevents abuse (imagine a 1MB title rendering on every
      thread card in the feed).
    - ``body``: 1-10,000 chars.  The generous max allows long-form posts
      while still preventing multi-megabyte payloads.
    - ``attachment_ids``: List of previously-uploaded file IDs to attach.
      ``max_length=20`` caps the number of attachments per thread to
      prevent abuse (uploading hundreds of files to a single thread).
    - ``tag_names``: Labels for the thread.  ``max_length=10`` prevents
      tag spam.  Tags are normalized to lowercase in the validator.
    """

    category_id: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=255)
    body: str = Field(min_length=1, max_length=10000)
    # max_length on a list field limits the NUMBER of items, not string length.
    # This prevents a client from attaching 1000 files to a single thread.
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)
    # Similarly, cap the number of tags to prevent tag spam / abuse.
    tag_names: list[str] = Field(default_factory=list, max_length=10)

    # -- XSS Prevention: sanitize the title before storage --
    # Strips <script> tags, javascript: URIs, onerror= handlers, etc.
    # Example attack prevented: title = '<img src=x onerror="steal(cookie)">'
    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        return sanitize_text(v)

    # -- XSS Prevention: sanitize the body before storage --
    # The body is rendered as content on the thread page.  Without this,
    # an attacker could embed <script> tags that execute for every viewer.
    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)

    # -- Tag Normalization & Sanitization --
    # Tags are lowercased for consistency ("Python" and "python" should be
    # the same tag), stripped of whitespace, and length-capped at 60 chars.
    # Each tag also passes through sanitize_text() to prevent XSS via tag
    # names that might be rendered as HTML.
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
    """
    Schema for editing an existing thread (PUT /api/v1/threads/{id}).

    Only ``title`` and ``body`` can be edited — the category, author,
    and metadata (locked/pinned status) are managed through other
    endpoints.  Both fields are sanitized identically to ThreadCreateRequest.
    """

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
    """
    Lightweight author info embedded in thread responses.

    Only includes the fields needed to render an author badge:
    ``id`` (for linking to profile), ``username``, ``role`` (for
    role flair like "Admin" or "Moderator"), and ``avatar_url``.
    """

    id: int
    username: str
    role: str
    avatar_url: str | None = None


class ThreadCategoryResponse(BaseModel):
    """
    Lightweight category info embedded in thread responses.

    ``slug`` is the URL-safe identifier (e.g., "backend-engineering")
    used in frontend routing.
    """

    id: int
    title: str
    slug: str


# ---------------------------------------------------------------------------
# Deferred imports: these schemas reference each other across modules.
# We import them here (after our own classes are defined) to avoid
# circular import errors.  This is a common pattern in Python when you
# have schemas that embed each other (Thread contains Posts, Posts
# contain Attachments, etc.).
# ---------------------------------------------------------------------------
from shared.schemas.post import PostResponse
from shared.schemas.upload import AttachmentResponse
from shared.schemas.vote import ReactionCountResponse
from shared.schemas.tag import TagResponse


class ThreadListItemResponse(BaseModel):
    """
    Schema for a single thread in the paginated feed.

    **Interview Concept: Computed / aggregated fields**

    Some fields here don't come directly from the ``threads`` database table:
    - ``reply_count`` — Computed by counting related posts.
    - ``vote_score`` — Sum of all votes (+1 and -1) on this thread.
    - ``user_vote`` — The current user's vote on this thread (0 if none).
      This allows the frontend to highlight the upvote/downvote button
      the user already clicked, without a separate API call.
    - ``reactions`` — Aggregated emoji reaction counts.
    - ``author`` / ``category`` — Joined from related tables.

    This "pre-joined" response pattern reduces the number of API calls
    the frontend needs to make — one request returns everything needed
    to render a thread card.
    """

    id: int
    title: str
    body: str
    is_locked: bool  # Locked threads reject new replies
    is_pinned: bool  # Pinned threads appear at the top
    created_at: datetime
    updated_at: datetime
    reply_count: int  # Total number of replies
    vote_score: int = 0  # Net score (upvotes - downvotes)
    user_vote: int = 0  # Current user's vote: -1, 0, or 1
    reactions: list[ReactionCountResponse] = []  # Emoji reaction summaries
    author: ThreadAuthorResponse  # Nested author info
    category: ThreadCategoryResponse  # Nested category info
    attachments: list[AttachmentResponse] = []  # Attached files
    tags: list[TagResponse] = []  # Topic tags/labels


class ThreadDetailResponse(ThreadListItemResponse):
    """
    Full thread detail including all replies (posts).

    Extends ``ThreadListItemResponse`` with a ``posts`` field containing
    the nested comment tree.  This schema is only used on the single-thread
    detail page, not in the feed — loading all replies for every thread
    in a paginated list would be extremely wasteful.

    **Interview Concept: Schema inheritance**

    Pydantic models support Python class inheritance.  By extending
    ``ThreadListItemResponse``, we inherit all its fields and just add
    ``posts``.  This avoids duplicating 15+ field definitions and ensures
    both schemas stay in sync.
    """

    posts: list[PostResponse]


class PaginatedThreadsResponse(BaseModel):
    """
    Paginated list of threads returned by GET /api/v1/threads.

    **Interview Concept: Cursor vs Offset Pagination**

    This uses **offset pagination** (page number + page size), which is
    simple to implement and understand:
    - ``page`` — Current page number (1-indexed).
    - ``page_size`` — Number of items per page.
    - ``total`` — Total number of matching threads across all pages.
    - ``total_pages`` — Computed as ``ceil(total / page_size)``.

    Offset pagination has a known limitation: if items are inserted/deleted
    between page requests, you might skip or duplicate items.  For a forum
    (low write frequency), this is acceptable.  High-throughput feeds
    (like Twitter) use **cursor-based pagination** instead.
    """

    items: list[ThreadListItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
