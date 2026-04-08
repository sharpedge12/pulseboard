"""
Tag Schemas
============

This module defines Pydantic models for forum tags — lightweight labels
that categorize threads by topic (e.g., "python", "react", "docker").

**Interview Concept: Normalization and canonicalization**

Tags are normalized to lowercase in the ``clean_name`` validator.  This
ensures that "Python", "PYTHON", and "python" all map to the same tag
in the database, preventing duplicate tags that differ only in case.

This is a common pattern called **canonicalization** — converting data to
a standard (canonical) form before storage.  Other examples:
- Email addresses: lowercased (``User@Example.COM`` → ``user@example.com``)
- URLs: trailing slashes removed (``/path/`` → ``/path``)
- Phone numbers: stripped to digits only

**Interview Concept: Why sanitize tag names?**

Tags are rendered as clickable badges in the thread feed.  If a tag name
contained ``<script>alert(1)</script>``, it would execute JavaScript in
every user's browser when the thread feed loads.  The ``sanitize_text()``
call strips dangerous HTML constructs while preserving normal text.
"""

from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class TagResponse(BaseModel):
    """
    Response schema for a tag.

    Simple id + name pair.  Tags are returned as nested lists inside
    ``ThreadListItemResponse.tags`` and ``ThreadDetailResponse.tags``.
    """

    id: int
    name: str


class TagCreateRequest(BaseModel):
    """
    Schema for creating a new tag (or referencing an existing one).

    The ``name`` field is 1-60 characters, normalized and sanitized:
    1. ``strip()`` — Remove leading/trailing whitespace.
    2. ``lower()`` — Normalize to lowercase for consistent matching.
    3. ``sanitize_text()`` — Strip dangerous HTML/script constructs.

    This triple sanitization happens in the ``clean_name`` validator
    below.  The order matters: strip first (so " Python " → "Python"),
    then lowercase ("Python" → "python"), then sanitize.
    """

    name: str = Field(min_length=1, max_length=60)

    # -- Normalization + XSS Prevention --
    # Three operations chained together:
    # 1. strip()        — Removes whitespace padding
    # 2. lower()        — Case-normalizes to prevent duplicate tags
    # 3. sanitize_text()— Removes <script> tags, javascript: URIs, etc.
    @field_validator("name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        return sanitize_text(v.strip().lower())
