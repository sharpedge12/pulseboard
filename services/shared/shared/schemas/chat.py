from datetime import datetime

from pydantic import BaseModel, Field


class ChatRoomCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    room_type: str = Field(pattern=r"^(direct|group)$")
    member_ids: list[int] = Field(default_factory=list)


class ChatMessageCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    reply_to_message_id: int | None = None
    attachment_ids: list[int] = Field(default_factory=list)


class ChatRoomMemberResponse(BaseModel):
    user_id: int
    username: str | None = None
    avatar_url: str | None = None


class ChatMessageSenderResponse(BaseModel):
    id: int
    username: str
    role: str
    avatar_url: str | None = None


class ChatMessageResponse(BaseModel):
    id: int
    room_id: int
    body: str
    reply_to_message_id: int | None
    created_at: datetime
    updated_at: datetime
    sender: ChatMessageSenderResponse
    attachments: list[dict[str, object]] = []


class ChatRoomResponse(BaseModel):
    id: int
    name: str
    display_name: str
    room_type: str
    created_at: datetime
    updated_at: datetime
    members: list[ChatRoomMemberResponse]
    last_message: ChatMessageResponse | None = None
