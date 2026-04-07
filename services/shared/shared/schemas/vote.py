from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class VoteRequest(BaseModel):
    value: int = Field(
        ...,
        ge=-1,
        le=1,
        description="1 for upvote, -1 for downvote (0 is not allowed)",
    )

    @field_validator("value")
    @classmethod
    def must_be_nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Vote value must be 1 or -1, not 0")
        return v


class VoteResponse(BaseModel):
    entity_type: str
    entity_id: int
    value: int
    vote_score: int


class ReactionRequest(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=32)


class ReactionCountResponse(BaseModel):
    emoji: str
    count: int


class ContentReportRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=2000)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        return sanitize_text(v)


class ContentReportResponse(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    reason: str


class VoterResponse(BaseModel):
    user_id: int
    username: str
    avatar_url: str | None = None
    value: int
