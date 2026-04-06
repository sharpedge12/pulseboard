from pydantic import BaseModel, Field


class VoteRequest(BaseModel):
    value: int = Field(..., ge=-1, le=1, description="1 for upvote, -1 for downvote")


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
