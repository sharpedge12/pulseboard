"""
Friendship Model — Friend Request State Machine
==================================================

Database tables defined here:
    - "friend_requests" -> FriendRequest (tracks friend relationships and their status)

FRIENDSHIP DATA MODEL:
    PulseBoard uses a REQUEST-BASED friendship model (like Facebook), not a
    FOLLOW model (like Twitter). The difference:
      - Follow model: User A follows User B. B doesn't need to approve.
        Unidirectional. One row per follow.
      - Friend model: User A sends a request to User B. B must accept.
        Bidirectional once accepted. One row per friend pair.

THE FRIEND REQUEST STATE MACHINE:
    A FriendRequest row transitions through these states:

        pending ──→ accepted
            │
            └──→ declined

    States:
      - PENDING:  A has requested friendship with B. B has not responded.
                  B sees this in their "Friend Requests" inbox.
      - ACCEPTED: B accepted the request. A and B are now mutual friends.
                  Both can see each other's online status, send DMs, etc.
      - DECLINED: B declined the request. The row is kept (not deleted) to
                  prevent A from spamming requests to B. The application
                  checks: if a declined request exists, don't allow a new one.

    WHY KEEP DECLINED ROWS?
        If we deleted declined requests, user A could send unlimited requests
        to user B (each time creating a new row). By keeping the declined row,
        the unique constraint + application logic prevents re-requesting.

WHY A UNIQUE CONSTRAINT ON (requester_id, recipient_id)?
    This ensures that for any ordered pair (A, B), only ONE friend request
    can exist. Without this, A could send B multiple pending requests.

    NOTE: This is a DIRECTIONAL constraint — (A, B) and (B, A) are different
    pairs. The application layer must check BOTH directions when determining
    if two users are already friends. For example, if A sent B a request,
    the row has requester_id=A, recipient_id=B. When checking if B and A are
    friends, you must query:
        WHERE (requester_id=A AND recipient_id=B)
           OR (requester_id=B AND recipient_id=A)

BIDIRECTIONAL FRIEND CHECK:
    Two users are "friends" if there exists a FriendRequest between them
    (in either direction) with status=ACCEPTED. The friendship is symmetric
    even though the table is asymmetric (one row, not two).
"""

from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class FriendRequestStatus(str, Enum):
    """
    Enum for the three possible states of a friend request.

    Inherits from str so values are JSON-serializable and comparable with
    plain strings (e.g., FriendRequestStatus.PENDING == "pending" is True).

    WHY AN ENUM?
        Restricts the status column to exactly these three values. Without it,
        a bug could insert "Accepted" (wrong case) or "cancelled" (nonexistent
        state). The database-level CHECK constraint (created by SqlEnum)
        provides a second line of defense.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class FriendRequest(TimestampMixin, Base):
    """
    Represents a friend request between two users, tracking its lifecycle.

    Database table: "friend_requests"

    Each row represents a directional request from requester -> recipient.
    Once accepted, the friendship is bidirectional (both users are friends),
    but the row remains directional in the database.

    Relationships:
        - requester: The user who sent the friend request (many-to-one)
        - recipient: The user who received the friend request (many-to-one)

    IMPORTANT — overlaps PARAMETER:
        Both relationships use overlaps="sent_friend_requests" / "received_..."
        This tells SQLAlchemy that these relationships intentionally overlap
        with the User model's sent_friend_requests and received_friend_requests
        relationships (which access the same FriendRequest rows through
        different FK paths). Without overlaps, SQLAlchemy raises a warning
        about potentially conflicting relationship configurations.
    """

    __tablename__ = "friend_requests"

    # The unique constraint prevents duplicate requests between the same pair.
    # Named constraints are important for migration tools and error messages.
    __table_args__ = (
        UniqueConstraint("requester_id", "recipient_id", name="uq_friend_request_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # The user who initiated the friend request.
    # CASCADE: if the requester's account is deleted, the request is deleted.
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # The user who received the friend request and must accept/decline.
    # CASCADE: if the recipient's account is deleted, the request is deleted.
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # The current state of the request. Uses SqlEnum which creates a CHECK
    # constraint in the database ensuring only valid status strings are stored.
    # Defaults to PENDING — a freshly created request hasn't been responded to yet.
    status: Mapped[FriendRequestStatus] = mapped_column(
        SqlEnum(FriendRequestStatus),
        default=FriendRequestStatus.PENDING,
    )

    # When the recipient responded (accepted or declined). NULL while the
    # request is still pending. This timestamp is useful for:
    #   1. Sorting friends by "most recently added"
    #   2. Audit trail — when did the friendship start?
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Both relationships point to User but via different FK columns.
    # foreign_keys=[...] disambiguates which column each relationship follows.
    # overlaps="..." silences SQLAlchemy warnings about overlapping paths.
    requester = relationship(
        "User", foreign_keys=[requester_id], overlaps="sent_friend_requests"
    )
    recipient = relationship(
        "User", foreign_keys=[recipient_id], overlaps="received_friend_requests"
    )
