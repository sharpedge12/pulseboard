"""
Forum Category (Community) Schemas
===================================

This module defines Pydantic models for forum categories (also called
"communities" in the UI).  Categories are the top-level organizational
unit — every thread belongs to exactly one category.

**Interview Concept: Slugs and URL-safe identifiers**

Categories have both a human-readable ``title`` ("Backend Engineering")
and a URL-safe ``slug`` ("backend-engineering").  The slug is used in
URLs like ``/community/backend-engineering`` instead of numeric IDs.

The slug pattern ``^[a-z0-9-]+$`` ensures slugs only contain lowercase
letters, digits, and hyphens.  This prevents:
- Spaces and special characters that would need URL encoding
- Uppercase letters that could cause case-sensitivity issues
- Characters like ``/`` or ``..`` that could enable path traversal

**Interview Concept: Schema inheritance**

``CommunityCreateRequest`` extends ``CategoryCreateRequest`` with no
additional fields.  This is a semantic alias — both schemas accept the
same data, but having a separate class allows the codebase to use
different names in different contexts (admin category creation vs
user community request) while sharing the same validation logic.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.services.sanitize import sanitize_text


class CategoryCreateRequest(BaseModel):
    """
    Schema for creating a new category (POST /api/v1/categories).

    Fields:
    - ``title``: Human-readable name, 3-120 chars.  Sanitized to prevent
      XSS when rendered in the category list.
    - ``slug``: URL-safe identifier, 3-120 chars.  The regex pattern
      ``^[a-z0-9-]+$`` is a whitelist that only allows lowercase
      alphanumeric characters and hyphens.  This is safer than a
      blacklist approach because it's impossible for any dangerous
      character to slip through.
    - ``description``: Optional text describing the category.  Sanitized
      because it's displayed in the category sidebar.
    """

    title: str = Field(min_length=3, max_length=120)
    # The slug regex uses a WHITELIST approach: only allow known-safe characters.
    # This is the gold standard for input validation — rather than trying to
    # block specific bad characters (which is error-prone), we only permit
    # the exact characters we want.
    slug: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=500)

    # -- XSS Prevention for category title --
    # Category titles appear in navigation menus, breadcrumbs, and sidebars.
    # A malicious title could execute scripts in every user's browser.
    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        return sanitize_text(v)

    # -- XSS Prevention for category description --
    # Description is displayed in the community info sidebar panel.
    # Handles None gracefully since description is optional.
    @field_validator("description")
    @classmethod
    def clean_description(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return sanitize_text(v)


class CategoryResponse(BaseModel):
    """
    Response schema for a single category.

    ``thread_count`` is a computed aggregate (COUNT of threads in this
    category) that the service layer calculates — it's not a direct
    database column.  This saves the frontend from making a separate
    API call to count threads per category.

    ``from_attributes=True`` enables Pydantic to read from SQLAlchemy
    model attributes rather than requiring a plain dict.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    slug: str
    description: str | None
    thread_count: int = 0


class CommunityCreateRequest(CategoryCreateRequest):
    """
    Alias for ``CategoryCreateRequest``, used in user-facing community
    creation flows.

    This is semantically identical to ``CategoryCreateRequest`` but exists
    as a separate class so that the codebase can distinguish between
    admin-created categories and user-requested communities in type hints
    and documentation.  All validation rules are inherited.
    """

    pass
