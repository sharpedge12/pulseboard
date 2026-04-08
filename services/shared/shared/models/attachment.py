"""
Attachment Model — File Upload Tracking
=========================================

Database table defined here:
    - "attachments" -> Attachment (metadata for uploaded files)

WHAT IS AN ATTACHMENT?
    When users upload files (images, documents, videos) to threads, posts, or
    chat messages, we need to track the metadata: who uploaded it, what it's
    linked to, the file name, type, size, and where it's stored on disk.

    The actual file bytes are stored on the FILESYSTEM (in an /uploads/
    directory), not in the database. This table stores only METADATA — a pointer
    to the file's location plus descriptive information.

WHY NOT STORE FILES IN THE DATABASE?
    Databases CAN store binary data (BLOB columns), but it's generally a bad
    practice for files because:
      1. Database backups become huge and slow.
      2. Serving files requires reading from the DB on every request (no CDN/
         reverse proxy caching).
      3. Database connections are expensive — tying one up to stream a 10 MB
         image wastes resources.
    The standard approach: store files on disk (or S3/cloud storage in
    production), store metadata in the database.

POLYMORPHIC LINK PATTERN:
    Like Vote and ContentReport, attachments use entity_type + entity_id to
    link to different content types (threads, posts, messages). This avoids
    having separate attachment tables for each content type.

    linked_entity_type values: "draft", "thread", "post", "message", "avatars"
    Validated by the upload endpoint to prevent arbitrary values.
"""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Attachment(TimestampMixin, Base):
    """
    Metadata record for an uploaded file (image, document, video).

    Database table: "attachments"

    Each row represents one uploaded file. The actual file is stored on the
    filesystem at the path specified in storage_path. This table provides
    the mapping between content entities and their attached files.

    DESIGN NOTES:
        - No ORM relationships defined here. Attachments are looked up via
          queries filtered on (linked_entity_type, linked_entity_id) rather
          than through SQLAlchemy relationship navigation. This keeps the model
          decoupled from the content models (Thread, Post, Message).
        - Inherits created_at and updated_at from TimestampMixin for tracking
          when files were uploaded and last modified.

    SECURITY:
        The upload system validates files before creating Attachment rows:
          1. File size check (max 25 MB)
          2. MIME type whitelist (images, videos, documents)
          3. Magic-byte validation (file header matches claimed MIME type)
          4. Extension whitelist (prevents double-extension attacks like .jpg.exe)
          5. Filename sanitization (strips path components, removes special chars)
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Who uploaded this file. CASCADE: deleting a user deletes their attachment
    # metadata (and the cleanup job should delete the actual files too).
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Polymorphic link — what type of content this file is attached to.
    # Values: "draft", "thread", "post", "message", "avatars".
    # Indexed for fast queries: "get all attachments for thread 42".
    linked_entity_type: Mapped[str] = mapped_column(String(50), index=True)

    # The PK of the linked entity. NOT a foreign key (polymorphic pattern —
    # it could point to threads, posts, or messages table).
    # Indexed because we almost always query by (entity_type, entity_id) together.
    linked_entity_id: Mapped[int] = mapped_column(Integer, index=True)

    # Original filename as uploaded by the user (after sanitization).
    # Preserved for display purposes ("Download report.pdf" vs "Download a1b2c3.pdf").
    file_name: Mapped[str] = mapped_column(String(255))

    # MIME type of the file, e.g., "image/jpeg", "application/pdf", "video/mp4".
    # Used for Content-Type headers when serving the file and for frontend
    # rendering decisions (display images inline, show PDF viewer, etc.).
    file_type: Mapped[str] = mapped_column(String(50))

    # File size in bytes. Used for:
    #   1. Display ("2.4 MB") in the UI
    #   2. Quota enforcement (if implemented)
    #   3. Deciding whether to inline-display or offer as download
    file_size: Mapped[int] = mapped_column(Integer)

    # Relative path to the file on disk, e.g., "uploads/attachments/a1b2c3.jpg".
    # The gateway proxies requests to /uploads/* to the Core service, which
    # serves the file from this path.
    # String(500) accommodates nested directory structures with long UUIDs.
    storage_path: Mapped[str] = mapped_column(String(500))
