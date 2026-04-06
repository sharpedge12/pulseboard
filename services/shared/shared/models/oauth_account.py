from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class OAuthAccount(TimestampMixin, Base):
    __tablename__ = "oauth_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(50), index=True)
    provider_user_id: Mapped[str] = mapped_column(String(255), index=True)
    provider_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user = relationship("User", back_populates="oauth_accounts")
