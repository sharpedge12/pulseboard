"""
Vote, Reaction & Content Report Schemas
========================================

This module defines Pydantic models for three related engagement features:

1. **Votes** — Reddit-style upvote/downvote on threads and posts.
2. **Reactions** — Emoji reactions (like GitHub/Slack-style reactions).
3. **Content Reports** — User-submitted reports of inappropriate content.

**Interview Concept: Why reject vote value 0?**

The ``VoteRequest`` schema accepts values -1 (downvote) and +1 (upvote),
but explicitly rejects 0.  Why?

- A vote of 0 is semantically meaningless — it's neither an upvote nor
  a downvote.  Accepting 0 would complicate the business logic: should
  it remove an existing vote?  That's what a DELETE endpoint is for.
- Using ``Field(ge=-1, le=1)`` alone would allow 0 because 0 is between
  -1 and 1.  The ``field_validator`` adds the additional business rule
  that 0 is not valid.
- This is a great example of **layered validation**: Pydantic's built-in
  ``ge``/``le`` constraints handle the range, and a custom validator
  handles the business rule.

**Interview Concept: Content moderation workflow**

Content reports follow a standard moderation workflow:
1. User creates a report (``ContentReportRequest``) with a reason.
2. Report is stored with status "pending".
3. A moderator/admin reviews the report and resolves it as either
   "resolved" (action taken) or "dismissed" (no action needed).
4. The ``ReportResolveRequest`` schema (in admin.py) validates the
   resolution status.
"""

from pydantic import BaseModel, Field, field_validator

from shared.services.sanitize import sanitize_text


class VoteRequest(BaseModel):
    """
    Schema for casting a vote on a thread or post.

    (POST /api/v1/threads/{id}/vote or /api/v1/threads/{id}/posts/{id}/vote)

    The ``value`` field uses two layers of validation:
    1. ``Field(ge=-1, le=1)`` — Pydantic ensures the value is in [-1, 0, 1].
    2. ``must_be_nonzero`` validator — Rejects 0 because a "neutral vote"
       has no meaning.  To remove a vote, use the DELETE endpoint.

    **Interview tip:** The ``...`` (Ellipsis) as the first argument to
    ``Field(...)`` means this field is **required** — there's no default
    value.  The client must explicitly send ``{"value": 1}`` or
    ``{"value": -1}``.
    """

    value: int = Field(
        ...,
        ge=-1,
        le=1,
        description="1 for upvote, -1 for downvote (0 is not allowed)",
    )

    # -- Business Rule: no neutral votes --
    # Pydantic's ge/le only constrain the range [-1, 1].  This validator
    # adds the semantic rule that 0 is not a valid vote.  Without this,
    # the API would accept {"value": 0} which doesn't mean anything in
    # a binary upvote/downvote system.
    @field_validator("value")
    @classmethod
    def must_be_nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("Vote value must be 1 or -1, not 0")
        return v


class VoteResponse(BaseModel):
    """
    Response returned after casting a vote.

    Includes both the specific vote info AND the updated aggregate
    ``vote_score`` — this lets the frontend update the UI immediately
    without a separate request to fetch the new score.

    - ``entity_type`` — "thread" or "post".
    - ``entity_id`` — ID of the thread/post that was voted on.
    - ``value`` — The vote that was just cast (1 or -1).
    - ``vote_score`` — Updated total score across all votes.
    """

    entity_type: str
    entity_id: int
    value: int
    vote_score: int


class ReactionRequest(BaseModel):
    """
    Schema for adding an emoji reaction to a thread or post.

    ``emoji`` is a short string (1-32 chars) that holds the emoji
    character or shortcode (e.g., "thumbsup", "heart", "fire").
    The frontend maps these to actual emoji glyphs for display.

    No sanitization is applied because emoji shortcodes are constrained
    by length and don't contain HTML constructs.
    """

    emoji: str = Field(..., min_length=1, max_length=32)


class ReactionCountResponse(BaseModel):
    """
    Aggregated reaction count for a single emoji on a thread or post.

    Example: ``{"emoji": "thumbsup", "count": 5}`` means 5 users reacted
    with the thumbs-up emoji.  The frontend renders these as clickable
    badges below the content.
    """

    emoji: str
    count: int


class ContentReportRequest(BaseModel):
    """
    Schema for reporting inappropriate content (threads, posts, or users).

    (POST /api/v1/threads/{id}/report, /api/v1/posts/{id}/report,
    or /api/v1/users/{id}/report)

    The ``reason`` field requires at least 3 characters to prevent
    trivial/spam reports.  It's sanitized because report reasons are
    displayed in the admin moderation dashboard — an unsanitized reason
    could contain XSS payloads that execute in a moderator's browser.

    **Interview Concept: Why sanitize admin-facing content?**

    It might seem like only public-facing content needs sanitization,
    but admin dashboards are actually high-value XSS targets.  A
    moderator's session typically has elevated privileges, so stealing
    their session cookie via XSS would give an attacker admin access.
    Always sanitize ALL user input, regardless of who will view it.
    """

    reason: str = Field(..., min_length=3, max_length=2000)

    # -- XSS Prevention for report reason --
    # Displayed in the admin dashboard where moderators review reports.
    # An attacker could craft a report specifically to execute scripts
    # in a moderator's browser session.
    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        return sanitize_text(v)


class ContentReportResponse(BaseModel):
    """
    Confirmation response after submitting a content report.

    Returns the report ID and the entity that was reported, so the
    frontend can show a "Report submitted" confirmation.
    """

    id: int
    entity_type: str  # "thread", "post", or "user"
    entity_id: int  # ID of the reported entity
    reason: str


class VoterResponse(BaseModel):
    """
    Individual voter info returned in the "who voted" popover.

    When a user clicks the vote score on a thread or post, the frontend
    shows a popover listing who voted and how.  This schema provides
    the minimal info needed: username, avatar, and vote direction.
    """

    user_id: int
    username: str
    avatar_url: str | None = None
    value: int  # 1 (upvoted) or -1 (downvoted)
