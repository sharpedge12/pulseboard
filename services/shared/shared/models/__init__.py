"""
PulseBoard Models — Barrel Export File (Package Initializer)
============================================================

Table of contents (database tables exported from this package):
    - Attachment        -> "attachments"
    - AuditLog          -> "audit_logs"
    - Category          -> "categories"
    - CategoryModerator -> "category_moderators"
    - CategoryRequest   -> "category_requests"
    - ChatRoom          -> "chat_rooms"
    - ChatRoomMember    -> "chat_room_members"
    - ContentReport     -> "content_reports"
    - EmailVerificationToken -> "email_verification_tokens"
    - FriendRequest     -> "friend_requests"
    - Message           -> "messages"
    - ModerationAction  -> "moderation_actions"
    - Notification      -> "notifications"
    - OAuthAccount      -> "oauth_accounts"
    - PasswordResetToken -> "password_reset_tokens"
    - Post              -> "posts"
    - Reaction          -> "reactions"
    - RefreshToken      -> "refresh_tokens"
    - Tag               -> "tags"
    - Thread            -> "threads"
    - ThreadSubscription -> "thread_subscriptions"
    - ThreadTag         -> "thread_tags"
    - TimestampMixin    -> (not a table — a reusable mixin)
    - User              -> "users"
    - Vote              -> "votes"

WHAT IS THE BARREL EXPORT PATTERN?
    A "barrel" is a single file (usually __init__.py) that re-exports symbols
    from many sub-modules. Instead of writing:

        from shared.models.user import User
        from shared.models.thread import Thread
        from shared.models.post import Post

    Consumers can write:

        from shared.models import User, Thread, Post

    This is cleaner, hides internal file organization, and lets you reorganize
    files without breaking imports across the codebase.

WHY __all__?
    The __all__ list explicitly declares which names are "public" when someone
    does `from shared.models import *`. Without __all__, a star-import would
    pull in EVERYTHING in this namespace (including internal helpers). __all__
    also serves as documentation — it's a quick reference of every model and
    enum the package provides.

WHY THIS MATTERS FOR DATABASE INITIALIZATION:
    In init_db() (shared/core/database.py), we do `import shared.models`. That
    single import triggers THIS file, which imports every model module. Each
    model class inherits from Base (SQLAlchemy's DeclarativeBase), and
    importing it causes SQLAlchemy to register the model in Base.metadata.
    Without this barrel import, create_all() wouldn't know about any tables.

    This is a common SQLAlchemy pattern: you must import all model classes
    before calling create_all(), and a barrel __init__.py is the cleanest
    way to guarantee that.
"""

from shared.models.attachment import Attachment
from shared.models.audit_log import AuditLog
from shared.models.base import TimestampMixin
from shared.models.category import Category
from shared.models.chat import ChatRoom, ChatRoomMember, Message
from shared.models.friendship import FriendRequest, FriendRequestStatus
from shared.models.notification import Notification
from shared.models.oauth_account import OAuthAccount
from shared.models.post import Post
from shared.models.tag import Tag, ThreadTag
from shared.models.thread import Thread, ThreadSubscription
from shared.models.user import (
    EmailVerificationToken,
    PasswordResetToken,
    RefreshToken,
    User,
    UserRole,
)
from shared.models.vote import (
    CategoryModerator,
    CategoryRequest,
    ContentReport,
    ModerationAction,
    Reaction,
    Vote,
)

__all__ = [
    "Attachment",
    "AuditLog",
    "Category",
    "CategoryModerator",
    "CategoryRequest",
    "ChatRoom",
    "ChatRoomMember",
    "ContentReport",
    "EmailVerificationToken",
    "FriendRequest",
    "FriendRequestStatus",
    "Message",
    "ModerationAction",
    "Notification",
    "OAuthAccount",
    "PasswordResetToken",
    "Post",
    "Reaction",
    "RefreshToken",
    "Tag",
    "Thread",
    "ThreadSubscription",
    "ThreadTag",
    "TimestampMixin",
    "User",
    "UserRole",
    "Vote",
]
