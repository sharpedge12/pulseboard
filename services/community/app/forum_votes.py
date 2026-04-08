"""
Forum Votes, Reactions, and Reports — User Engagement Logic.

This module handles three distinct user-engagement features that share a
common polymorphic pattern (they all operate on either a *thread* or a
*post*, identified by ``entity_type`` + ``entity_id``):

1. **Votes** (upvote / downvote):
   Reddit-style voting where each user may cast exactly one vote (+1 or -1)
   per entity.  Clicking the same vote direction twice *removes* the vote
   (toggle behaviour).  The aggregate score is ``SUM(value)`` across all
   votes for that entity.

2. **Reactions** (emoji):
   Slack/Discord-style emoji reactions.  Each user can add one instance of
   each emoji per entity.  Adding the same emoji again removes it (toggle).
   Reactions are counted per-emoji and displayed as badges on the frontend.

3. **Reports** (content flagging):
   Users can flag inappropriate content for moderator review.  A duplicate
   check prevents the same user from reporting the same entity twice.

Design patterns to note for interviews:
    - **Upsert / toggle pattern** in ``cast_vote`` and ``toggle_reaction``:
      check for an existing row, then decide whether to insert, update, or
      delete.  This avoids unique-constraint violations and provides
      idiomatic toggle semantics.
    - **Polymorphic association**: ``entity_type`` (``"thread"`` or ``"post"``)
      plus ``entity_id`` lets one table serve multiple parent types without
      separate join tables for each.
    - **Bulk helpers** (``get_vote_scores_bulk``, ``get_reaction_counts_bulk``):
      batch-fetch scores for a list of IDs in a single SQL query to avoid
      the N+1 query problem when rendering thread lists.

Called from:
    ``app.forum_routes`` (vote / react / report endpoints on threads and
    posts) and ``app.forum_services`` (inline score lookups for serialisation).
"""

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from shared.models.post import Post
from shared.models.thread import Thread
from shared.models.vote import ContentReport, Reaction, Vote
from shared.models.user import User
from shared.schemas.vote import (
    ContentReportResponse,
    ReactionCountResponse,
    VoterResponse,
    VoteResponse,
)
from shared.services.audit import record as audit_record
from shared.services import audit as audit_actions


# ---------------------------------------------------------------------------
# Helper — existence check
# ---------------------------------------------------------------------------


def _verify_entity_exists(db: Session, entity_type: str, entity_id: int) -> None:
    """
    Raise HTTP 404 if the target thread or post does not exist.

    This guard is called before any vote, reaction, or report operation
    so that we never create orphan records pointing at non-existent content.

    Args:
        db: Active database session.
        entity_type: ``"thread"`` or ``"post"``.
        entity_id: Primary key of the target entity.

    Raises:
        HTTPException(400) for unsupported entity types.
        HTTPException(404) if the entity row is missing.
    """
    if entity_type == "thread":
        exists = db.execute(
            select(Thread.id).where(Thread.id == entity_id)
        ).scalar_one_or_none()
    elif entity_type == "post":
        exists = db.execute(
            select(Post.id).where(Post.id == entity_id)
        ).scalar_one_or_none()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported entity type: {entity_type}",
        )
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type.capitalize()} not found.",
        )


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------


def cast_vote(
    db: Session,
    user_id: int,
    entity_type: str,
    entity_id: int,
    value: int,
) -> VoteResponse:
    """
    Cast, flip, or remove a vote on a thread or post.

    This implements the classic Reddit-style vote toggle with three states
    per user per entity:

        - No vote exists → INSERT a new vote row.
        - Vote exists with the SAME value → DELETE the vote (un-vote).
        - Vote exists with the OPPOSITE value → UPDATE the value (flip).

    Why this pattern?
        A simple INSERT would fail if the user already voted (unique
        constraint).  A simple UPDATE would fail if no row exists yet.
        The upsert-or-toggle approach handles all transitions cleanly.

    Args:
        db: Active database session.
        user_id: ID of the voting user.
        entity_type: ``"thread"`` or ``"post"``.
        entity_id: Primary key of the target entity.
        value: ``1`` (upvote) or ``-1`` (downvote).

    Returns:
        ``VoteResponse`` containing the user's current vote value (``0`` if
        removed) and the entity's new aggregate vote score.
    """
    if value not in (1, -1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vote value must be 1 or -1.",
        )

    _verify_entity_exists(db, entity_type, entity_id)

    # Look up whether this user already has a vote on this entity.
    existing = db.execute(
        select(Vote).where(
            Vote.user_id == user_id,
            Vote.entity_type == entity_type,
            Vote.entity_id == entity_id,
        )
    ).scalar_one_or_none()

    if existing:
        if existing.value == value:
            # Same direction clicked again → remove the vote (toggle off).
            db.delete(existing)
            db.commit()
            score = _get_vote_score(db, entity_type, entity_id)
            return VoteResponse(
                entity_type=entity_type,
                entity_id=entity_id,
                value=0,  # User's vote is now cleared
                vote_score=score,
            )
        # Different direction → flip the vote (e.g. upvote → downvote).
        existing.value = value
    else:
        # No existing vote → create a new one.
        existing = Vote(
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            value=value,
        )
        db.add(existing)

    db.commit()
    score = _get_vote_score(db, entity_type, entity_id)
    return VoteResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        value=value,
        vote_score=score,
    )


def remove_vote(
    db: Session,
    user_id: int,
    entity_type: str,
    entity_id: int,
) -> VoteResponse:
    """
    Explicitly remove a user's vote on a thread or post.

    Unlike ``cast_vote`` (which toggles), this endpoint unconditionally
    deletes any existing vote.  Used by the ``DELETE /threads/{id}/vote``
    and ``DELETE /posts/{id}/vote`` routes.

    Returns:
        ``VoteResponse`` with ``value=0`` and the updated aggregate score.
    """
    db.execute(
        delete(Vote).where(
            Vote.user_id == user_id,
            Vote.entity_type == entity_type,
            Vote.entity_id == entity_id,
        )
    )
    db.commit()
    score = _get_vote_score(db, entity_type, entity_id)
    return VoteResponse(
        entity_type=entity_type,
        entity_id=entity_id,
        value=0,
        vote_score=score,
    )


def _get_vote_score(db: Session, entity_type: str, entity_id: int) -> int:
    """
    Compute the aggregate vote score for a single entity.

    The score is ``SUM(value)`` across all votes, where each vote is +1
    or -1.  ``COALESCE(…, 0)`` ensures we return 0 (not NULL) when there
    are no votes yet.

    Returns:
        Integer score (can be negative if downvotes exceed upvotes).
    """
    result = db.execute(
        select(func.coalesce(func.sum(Vote.value), 0)).where(
            Vote.entity_type == entity_type,
            Vote.entity_id == entity_id,
        )
    ).scalar()
    return int(result)


def get_user_vote(db: Session, user_id: int, entity_type: str, entity_id: int) -> int:
    """
    Look up the current user's vote on a specific entity.

    Returns:
        ``1`` (upvoted), ``-1`` (downvoted), or ``0`` (no vote).
    """
    vote = db.execute(
        select(Vote.value).where(
            Vote.user_id == user_id,
            Vote.entity_type == entity_type,
            Vote.entity_id == entity_id,
        )
    ).scalar_one_or_none()
    return vote or 0


def get_vote_scores_bulk(
    db: Session, entity_type: str, entity_ids: list[int]
) -> dict[int, int]:
    """
    Batch-fetch aggregate vote scores for multiple entities in ONE query.

    This is a critical optimisation for the thread listing page: instead
    of running N separate ``_get_vote_score`` calls (one per thread), we
    run a single ``GROUP BY`` query.

    Args:
        db: Active database session.
        entity_type: ``"thread"`` or ``"post"``.
        entity_ids: List of entity primary keys to score.

    Returns:
        Dict mapping ``entity_id → score``.  Entities with zero votes
        will be absent from the dict (callers should use ``.get(id, 0)``).
    """
    if not entity_ids:
        return {}
    rows = db.execute(
        select(Vote.entity_id, func.sum(Vote.value))
        .where(Vote.entity_type == entity_type, Vote.entity_id.in_(entity_ids))
        .group_by(Vote.entity_id)
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def get_user_votes_bulk(
    db: Session, user_id: int, entity_type: str, entity_ids: list[int]
) -> dict[int, int]:
    """
    Batch-fetch a user's votes on multiple entities in ONE query.

    Used to show the user which threads/posts they have already voted on
    (highlight the upvote/downvote buttons).

    Returns:
        Dict mapping ``entity_id → vote_value`` (1 or -1).
    """
    if not entity_ids or not user_id:
        return {}
    rows = db.execute(
        select(Vote.entity_id, Vote.value).where(
            Vote.user_id == user_id,
            Vote.entity_type == entity_type,
            Vote.entity_id.in_(entity_ids),
        )
    ).all()
    return {row[0]: int(row[1]) for row in rows}


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


def toggle_reaction(
    db: Session,
    user_id: int,
    entity_type: str,
    entity_id: int,
    emoji: str,
) -> list[ReactionCountResponse]:
    """
    Add or remove an emoji reaction on a thread or post (toggle pattern).

    If the user has already reacted with this emoji → remove the reaction.
    If not → add it.  This mirrors how Discord/Slack reactions work: click
    once to react, click again to un-react.

    After toggling, the function returns the updated reaction counts for
    ALL emojis on this entity, so the frontend can re-render the entire
    reaction bar in one shot.

    Args:
        db: Active database session.
        user_id: Reacting user's ID.
        entity_type: ``"thread"`` or ``"post"``.
        entity_id: Target entity's primary key.
        emoji: The emoji string (e.g. ``"👍"``, ``"🔥"``).

    Returns:
        List of ``ReactionCountResponse`` objects, one per distinct emoji,
        ordered by popularity (highest count first).
    """
    _verify_entity_exists(db, entity_type, entity_id)

    # Check if this user already has this specific emoji reaction.
    existing = db.execute(
        select(Reaction).where(
            Reaction.user_id == user_id,
            Reaction.entity_type == entity_type,
            Reaction.entity_id == entity_id,
            Reaction.emoji == emoji,
        )
    ).scalar_one_or_none()

    if existing:
        # Already reacted with this emoji → remove it (toggle off).
        db.delete(existing)
    else:
        # Not yet reacted → add the reaction (toggle on).
        db.add(
            Reaction(
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                emoji=emoji,
            )
        )
    db.commit()

    # Return the full updated reaction counts for this entity.
    return get_reaction_counts(db, entity_type, entity_id)


def get_reaction_counts(
    db: Session, entity_type: str, entity_id: int
) -> list[ReactionCountResponse]:
    """
    Get the count of each emoji reaction on a single entity.

    Returns:
        Sorted list (most popular emoji first) of
        ``ReactionCountResponse(emoji, count)`` objects.
    """
    rows = db.execute(
        select(Reaction.emoji, func.count(Reaction.id))
        .where(Reaction.entity_type == entity_type, Reaction.entity_id == entity_id)
        .group_by(Reaction.emoji)
        .order_by(func.count(Reaction.id).desc())
    ).all()
    return [ReactionCountResponse(emoji=row[0], count=row[1]) for row in rows]


def get_reaction_counts_bulk(
    db: Session, entity_type: str, entity_ids: list[int]
) -> dict[int, list[ReactionCountResponse]]:
    """
    Batch-fetch reaction counts for multiple entities in ONE query.

    Similar rationale to ``get_vote_scores_bulk``: avoids N+1 queries when
    rendering a list of threads or posts.

    Returns:
        Dict mapping ``entity_id → list[ReactionCountResponse]``.
        Entities with no reactions will be absent from the dict.
    """
    if not entity_ids:
        return {}
    rows = db.execute(
        select(Reaction.entity_id, Reaction.emoji, func.count(Reaction.id))
        .where(Reaction.entity_type == entity_type, Reaction.entity_id.in_(entity_ids))
        .group_by(Reaction.entity_id, Reaction.emoji)
        .order_by(Reaction.entity_id, func.count(Reaction.id).desc())
    ).all()

    # Group reaction counts by entity_id.
    result: dict[int, list[ReactionCountResponse]] = {}
    for entity_id, emoji, count in rows:
        result.setdefault(entity_id, []).append(
            ReactionCountResponse(emoji=emoji, count=count)
        )
    return result


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def report_content(
    db: Session,
    reporter_id: int,
    entity_type: str,
    entity_id: int,
    reason: str,
) -> ContentReportResponse:
    """
    File a content report (flag) against a thread or post.

    Reports are queued for moderator review in the admin dashboard.  Each
    user can only report a given entity once (enforced by a duplicate
    check, not a database unique constraint).

    Flow:
        1. Verify the target entity exists.
        2. Check for an existing report from the same user on the same
           entity — return 409 Conflict if found (prevents spam).
        3. Create the ``ContentReport`` row with status ``"pending"``.
        4. Record an audit log entry for traceability.
        5. Commit and return the serialised report.

    Args:
        reporter_id: ID of the user filing the report.
        entity_type: ``"thread"`` or ``"post"``.
        entity_id: ID of the content being reported.
        reason: Free-text explanation from the reporter.

    Returns:
        ``ContentReportResponse`` with the created report's details.

    Raises:
        HTTPException(409) if the user has already reported this content.
    """
    _verify_entity_exists(db, entity_type, entity_id)

    # Duplicate check — prevent the same user from flooding the report queue
    # with repeated reports on the same content.
    existing = db.execute(
        select(ContentReport).where(
            ContentReport.reporter_id == reporter_id,
            ContentReport.entity_type == entity_type,
            ContentReport.entity_id == entity_id,
        )
    ).scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already reported this content.",
        )

    report = ContentReport(
        reporter_id=reporter_id,
        entity_type=entity_type,
        entity_id=entity_id,
        reason=reason,
    )
    db.add(report)
    db.flush()  # Flush to get the auto-generated report.id for the audit log.

    # Record the report creation in the audit trail for accountability.
    audit_record(
        db,
        actor_id=reporter_id,
        action=audit_actions.REPORT_CREATE,
        entity_type="report",
        entity_id=report.id,
        details={"entity_type": entity_type, "entity_id": entity_id, "reason": reason},
    )
    db.commit()
    db.refresh(report)

    return ContentReportResponse(
        id=report.id,
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        reason=report.reason,
    )


def get_voters(
    db: Session,
    entity_type: str,
    entity_id: int,
) -> list[VoterResponse]:
    """
    List all users who voted on a specific thread or post, along with
    their vote direction.

    Used by the frontend's "voters popover" that appears when a user
    clicks/hovers on the vote score to see who upvoted or downvoted.

    Returns:
        List of ``VoterResponse`` objects sorted by most recent vote first.
    """
    rows = db.execute(
        select(Vote, User)
        .join(User, User.id == Vote.user_id)
        .where(
            Vote.entity_type == entity_type,
            Vote.entity_id == entity_id,
        )
        .order_by(Vote.created_at.desc())
    ).all()
    return [
        VoterResponse(
            user_id=user.id,
            username=user.username,
            avatar_url=user.avatar_url,
            value=vote.value,
        )
        for vote, user in rows
    ]
