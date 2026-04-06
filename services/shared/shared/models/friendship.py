from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class FriendRequestStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class FriendRequest(TimestampMixin, Base):
    __tablename__ = "friend_requests"
    __table_args__ = (
        UniqueConstraint("requester_id", "recipient_id", name="uq_friend_request_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    status: Mapped[FriendRequestStatus] = mapped_column(
        SqlEnum(FriendRequestStatus),
        default=FriendRequestStatus.PENDING,
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    requester = relationship(
        "User", foreign_keys=[requester_id], overlaps="sent_friend_requests"
    )
    recipient = relationship(
        "User", foreign_keys=[recipient_id], overlaps="received_friend_requests"
    )
