from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from shared.services.sanitize import sanitize_text, sanitize_username


class UserUpdateRequest(BaseModel):
    username: str | None = Field(
        default=None, min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$"
    )
    bio: str | None = Field(default=None, max_length=500)

    @field_validator("username")
    @classmethod
    def clean_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return sanitize_username(v)

    @field_validator("bio")
    @classmethod
    def clean_bio(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return sanitize_text(v)


class UserMeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: EmailStr
    role: str
    is_verified: bool
    is_active: bool
    is_suspended: bool
    is_banned: bool
    bio: str | None = None
    avatar_url: str | None = None
    created_at: datetime | None = None
    last_seen: datetime | None = None


class UserListItemResponse(BaseModel):
    id: int
    username: str
    email: str
    role: str
    is_verified: bool
    bio: str | None = None
    avatar_url: str | None = None
    friendship_status: str = "none"
    created_at: datetime | None = None
    last_seen: datetime | None = None
    is_online: bool = False


class UserPublicProfileResponse(BaseModel):
    id: int
    username: str
    role: str
    is_verified: bool
    bio: str | None = None
    avatar_url: str | None = None
    friendship_status: str = "none"
    created_at: datetime | None = None
    last_seen: datetime | None = None
    is_online: bool = False


class UserReportRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        return sanitize_text(v)


class UserActionResponse(BaseModel):
    message: str


class FriendRequestResponse(BaseModel):
    id: int
    status: str
    user: UserPublicProfileResponse


class FriendRequestListResponse(BaseModel):
    incoming: list[FriendRequestResponse]
    outgoing: list[FriendRequestResponse]
    friends: list[UserPublicProfileResponse]
