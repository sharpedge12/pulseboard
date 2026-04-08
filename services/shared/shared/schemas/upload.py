"""
File Upload Schemas
====================

This module defines Pydantic models for file upload responses.

**Interview Concept: Uploads are decoupled from content creation**

The upload flow in PulseBoard is a **two-step process**:

1. **Upload the file** (POST /api/v1/uploads) — Returns an ``UploadResponse``
   with the file's ``id``, ``public_url``, and metadata.
2. **Attach the file to content** — When creating a thread, post, or chat
   message, the client sends the upload ``id`` in the ``attachment_ids``
   list.  The server links the upload to the content entity.

This decoupled approach has several advantages:
- **UX**: Files upload immediately (with progress bars), before the user
  finishes writing their post.  No waiting after clicking "Submit".
- **Simplicity**: The upload endpoint handles file validation, virus
  scanning, and storage.  The content creation endpoint just links IDs.
- **Reusability**: The same upload endpoint serves threads, posts, chat
  messages, and avatars.

**Interview Concept: Why ``linked_entity_type`` and ``linked_entity_id``?**

These fields implement a **polymorphic association** — a single ``uploads``
table can reference different entity types (threads, posts, messages,
avatars) without separate foreign key columns for each.  The trade-off
is that you lose database-level referential integrity (no FK constraint),
but you gain schema simplicity and flexibility.

**Interview Concept: ``AttachmentResponse`` as a semantic alias**

``AttachmentResponse`` is identical to ``UploadResponse`` but exists as
a separate class for clarity in type hints.  When you see
``list[AttachmentResponse]`` in a thread/post response, it's immediately
clear these are *attached files*, not arbitrary upload records.
"""

from datetime import datetime

from pydantic import BaseModel


class UploadResponse(BaseModel):
    """
    Response returned after a successful file upload.

    Contains all metadata about the uploaded file:
    - ``file_name`` — Original filename (sanitized on upload to prevent
      directory traversal attacks like ``../../etc/passwd``).
    - ``file_type`` — MIME type (e.g., "image/png", "application/pdf").
      Validated against a whitelist on upload; also verified via
      magic-byte file signature analysis to prevent MIME spoofing.
    - ``file_size`` — Size in bytes.  Enforced against a maximum (25 MB)
      during upload.
    - ``storage_path`` — Server-side file path (relative to uploads dir).
    - ``public_url`` — URL the frontend uses to display/download the file.
      Proxied through the gateway: ``/uploads/{path}``.
    - ``linked_entity_type`` — What this file is attached to: "draft",
      "thread", "post", "message", or "avatars".
    - ``linked_entity_id`` — ID of the linked entity (0 for drafts that
      haven't been attached yet).
    """

    id: int
    file_name: str
    file_type: str  # MIME type (validated on upload)
    file_size: int  # Size in bytes
    storage_path: str  # Server-side storage path
    public_url: str  # Client-accessible URL
    linked_entity_type: str  # "draft", "thread", "post", "message", "avatars"
    linked_entity_id: int  # ID of the entity this file is attached to
    created_at: datetime


class AttachmentResponse(UploadResponse):
    """
    Semantic alias for ``UploadResponse``, used in thread/post/message
    responses to represent attached files.

    Inherits all fields from ``UploadResponse`` without adding any new
    ones.  This exists purely for readability and type-hint clarity:
    ``list[AttachmentResponse]`` is more descriptive than
    ``list[UploadResponse]`` when the context is "files attached to a post".
    """

    pass


class UploadLimitsResponse(BaseModel):
    """
    Response for GET /api/v1/uploads/limits — tells the frontend what
    file types and sizes are allowed.

    The frontend uses this to:
    1. Set the ``accept`` attribute on ``<input type="file">`` elements
       (so the OS file picker only shows allowed file types).
    2. Run client-side validation before uploading (show an error
       immediately if the file is too large, rather than uploading 25 MB
       and getting a 413 error).

    - ``allowed_types`` — List of MIME types (e.g., ["image/png",
      "image/jpeg", "application/pdf"]).
    - ``max_upload_mb`` — Maximum file size in megabytes.
    """

    allowed_types: list[str]
    max_upload_mb: int
