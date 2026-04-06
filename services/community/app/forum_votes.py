"""Vote, reaction, and report logic for the forum service."""

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


def _verify_entity_exists(db: Session, entity_type: str, entity_id: int) -> None:
    """Raise 404 if the target entity does not exist."""
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
    if value not in (1, -1):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vote value must be 1 or -1.",
        )

    _verify_entity_exists(db, entity_type, entity_id)

    existing = db.execute(
        select(Vote).where(
            Vote.user_id == user_id,
            Vote.entity_type == entity_type,
            Vote.entity_id == entity_id,
        )
    ).scalar_one_or_none()

    if existing:
        if existing.value == value:
            db.delete(existing)
            db.commit()
            score = _get_vote_score(db, entity_type, entity_id)
            return VoteResponse(
                entity_type=entity_type,
                entity_id=entity_id,
                value=0,
                vote_score=score,
            )
        existing.value = value
    else:
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
    result = db.execute(
        select(func.coalesce(func.sum(Vote.value), 0)).where(
            Vote.entity_type == entity_type,
            Vote.entity_id == entity_id,
        )
    ).scalar()
    return int(result)


def get_user_vote(db: Session, user_id: int, entity_type: str, entity_id: int) -> int:
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
    _verify_entity_exists(db, entity_type, entity_id)

    existing = db.execute(
        select(Reaction).where(
            Reaction.user_id == user_id,
            Reaction.entity_type == entity_type,
            Reaction.entity_id == entity_id,
            Reaction.emoji == emoji,
        )
    ).scalar_one_or_none()

    if existing:
        db.delete(existing)
    else:
        db.add(
            Reaction(
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                emoji=emoji,
            )
        )
    db.commit()
    return get_reaction_counts(db, entity_type, entity_id)


def get_reaction_counts(
    db: Session, entity_type: str, entity_id: int
) -> list[ReactionCountResponse]:
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
    if not entity_ids:
        return {}
    rows = db.execute(
        select(Reaction.entity_id, Reaction.emoji, func.count(Reaction.id))
        .where(Reaction.entity_type == entity_type, Reaction.entity_id.in_(entity_ids))
        .group_by(Reaction.entity_id, Reaction.emoji)
        .order_by(Reaction.entity_id, func.count(Reaction.id).desc())
    ).all()
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
    _verify_entity_exists(db, entity_type, entity_id)

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
    db.flush()
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
