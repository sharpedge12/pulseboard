"""
Voting, Reactions, Moderation & Community Management Models
============================================================

Database tables defined here:
    - "votes"               -> Vote (Reddit-style +1/-1 upvote/downvote)
    - "reactions"            -> Reaction (emoji reactions on content)
    - "content_reports"      -> ContentReport (user-submitted reports)
    - "moderation_actions"   -> ModerationAction (warn/suspend/ban records)
    - "category_moderators"  -> CategoryModerator (moderator-category assignments)
    - "category_requests"    -> CategoryRequest (requests to create new categories)

This file covers two major areas:
  1. USER ENGAGEMENT: Votes and reactions (how users interact with content)
  2. CONTENT MODERATION: Reports, actions, and moderator assignments (how staff
     maintain community health)

POLYMORPHIC ENTITY PATTERN:
    Several models here (Vote, Reaction, ContentReport) use the same pattern:
        entity_type: String  ("thread" or "post")
        entity_id:   Integer (the ID of the thread or post)

    This is called the "polymorphic association" or "generic foreign key" pattern.
    Instead of having separate thread_votes and post_votes tables (which would
    duplicate logic), we use ONE table with a type+id pair.

    Trade-offs:
      + Fewer tables, less code duplication, unified queries
      + Easy to extend (adding "message" voting just means a new entity_type value)
      - No database-level foreign key constraint (the DB can't enforce that
        entity_id actually exists in the correct table)
      - JOINs are more complex (need to join conditionally based on entity_type)

    This is the same pattern used by Django's ContentType framework, Rails'
    polymorphic associations, and many real-world systems.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.core.database import Base
from shared.models.base import TimestampMixin


class Vote(TimestampMixin, Base):
    """
    Reddit-style upvote/downvote on a thread or post.

    Database table: "votes"

    HOW REDDIT-STYLE VOTING WORKS:
        - Each user can cast exactly ONE vote per entity (thread or post).
        - The vote value is +1 (upvote) or -1 (downvote).
        - The "score" of a thread/post is SUM(value) across all votes.
        - A user can change their vote (toggle from +1 to -1 or remove it).

    WHY THE UNIQUE CONSTRAINT?
        UniqueConstraint("user_id", "entity_type", "entity_id") ensures each
        user can only have ONE vote per entity. Without this:
          - A user could upvote the same post 1000 times
          - The application would need to check for existing votes before
            every insert (race conditions!)
        The database constraint is the authoritative guard against double-voting.

    WHY SmallInteger FOR value?
        SmallInteger uses 2 bytes instead of Integer's 4 bytes. Since we only
        store +1 or -1, this saves space. With millions of votes, this adds up.
        The application layer validates that value is exactly +1 or -1 via
        Pydantic schema validation.

    WHY entity_type + entity_id INSTEAD OF thread_id + post_id?
        See the "Polymorphic Entity Pattern" in the module docstring. One table
        handles votes on both threads and posts, reducing code duplication.
    """

    __tablename__ = "votes"
    __table_args__ = (
        # Composite unique constraint: one vote per user per entity.
        # Named "uq_vote_user_entity" for migration/error message clarity.
        UniqueConstraint(
            "user_id", "entity_type", "entity_id", name="uq_vote_user_entity"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # The user who cast this vote. CASCADE: deleting a user removes their votes.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # "thread" or "post" — identifies which table entity_id refers to.
    entity_type: Mapped[str] = mapped_column(String(10))  # 'thread' or 'post'

    # The PK of the thread or post being voted on. NOT a foreign key (because
    # it could point to either table — see polymorphic pattern discussion).
    entity_id: Mapped[int] = mapped_column(Integer)

    # +1 for upvote, -1 for downvote. Never 0 (Pydantic rejects value=0).
    value: Mapped[int] = mapped_column(SmallInteger)  # +1 or -1


class Reaction(TimestampMixin, Base):
    """
    Emoji reaction on a thread or post (like GitHub's reactions or Slack's emoji).

    Database table: "reactions"

    Unlike votes (which are +1/-1), reactions are emoji strings. A user can
    react with DIFFERENT emojis to the same entity (e.g., both "thumbs_up" and
    "heart" on the same post), but cannot react with the SAME emoji twice.

    The unique constraint includes the emoji field — (user, type, id, emoji)
    — which is what allows multiple different emojis but prevents duplicates
    of the same emoji from the same user.
    """

    __tablename__ = "reactions"
    __table_args__ = (
        # A user can add different emojis to the same entity, but not the
        # same emoji twice. The 4-column constraint covers this precisely.
        UniqueConstraint(
            "user_id",
            "entity_type",
            "entity_id",
            "emoji",
            name="uq_reaction_user_entity_emoji",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(10))  # 'thread' or 'post'
    entity_id: Mapped[int] = mapped_column(Integer)

    # The emoji identifier string, e.g., "thumbs_up", "heart", "fire".
    # String(32) accommodates emoji shortcodes. We store shortcodes (not
    # actual Unicode emoji characters) for consistent cross-platform rendering.
    emoji: Mapped[str] = mapped_column(String(32))


class ContentReport(TimestampMixin, Base):
    """
    A user-submitted report flagging content (thread, post, or user) for review.

    Database table: "content_reports"

    CONTENT MODERATION WORKFLOW:
        1. A user sees offensive content and clicks "Report".
        2. A ContentReport row is created with status="pending".
        3. Moderators/admins view pending reports in the admin dashboard.
        4. A moderator reviews the report and either:
           a. RESOLVES it (takes action: warn, suspend, or ban the offender)
           b. DISMISSES it (decides the report is invalid/unfounded)
        5. The status is updated to "resolved" or "dismissed", with the
           reviewer's ID and timestamp recorded.

    WHY THE UNIQUE CONSTRAINT?
        UniqueConstraint("reporter_id", "entity_type", "entity_id") prevents
        a user from reporting the same content multiple times. One report per
        user per entity is sufficient — multiple reports from different users
        on the same content will each be tracked separately, giving moderators
        a signal of how many people flagged it.

    Relationships:
        - reporter: The user who submitted the report (many-to-one)
        - resolver: The moderator/admin who resolved the report (many-to-one)
    """

    __tablename__ = "content_reports"
    __table_args__ = (
        # One report per user per entity — prevents spam-reporting.
        UniqueConstraint(
            "reporter_id",
            "entity_type",
            "entity_id",
            name="uq_report_user_entity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Who submitted the report. CASCADE: if the reporter is deleted, their
    # reports are cleaned up (debatable — some systems preserve reports even
    # after the reporter leaves).
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    # Polymorphic entity reference — "thread", "post", or "user".
    entity_type: Mapped[str] = mapped_column(String(10))  # 'thread', 'post', or 'user'
    entity_id: Mapped[int] = mapped_column(Integer)

    # Free-text reason from the reporter explaining why the content is problematic.
    reason: Mapped[str] = mapped_column(Text)

    # Report status: starts as "pending", transitions to "resolved" or "dismissed".
    # String(20) + default="pending" — we use a string instead of an enum here
    # for flexibility (new statuses could be added without a migration).
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending/resolved/dismissed

    # WHO resolved the report (a moderator or admin). SET NULL on delete: if
    # the resolver's account is later deleted, the report record survives with
    # resolved_by=NULL. This preserves the moderation history.
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # WHEN the report was resolved. NULL means it's still pending.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Two relationships to the User table — SQLAlchemy needs foreign_keys=[]
    # to disambiguate which FK each relationship follows, since both reporter_id
    # and resolved_by point to users.id.
    reporter = relationship("User", foreign_keys=[reporter_id])
    resolver = relationship("User", foreign_keys=[resolved_by])


class ModerationAction(TimestampMixin, Base):
    """
    Records a moderation action (warn, suspend, ban) taken against a user.

    Database table: "moderation_actions"

    PURPOSE:
        Every time a moderator/admin takes punitive action against a user,
        it's recorded here for accountability and transparency. This creates
        a history that admins can review:
          - "User X has been warned 3 times this month"
          - "Moderator Y banned 10 users yesterday — is that normal?"

    ACTION TYPES:
        - "warn":    Informational — the user receives a notification but no
                     restriction is applied. Three warnings might escalate to
                     a suspension.
        - "suspend": Temporary restriction — the user can read but not post
                     for duration_hours. The User.is_suspended flag is set.
        - "ban":     Permanent lockout — the user cannot log in. The
                     User.is_banned flag is set.

    Relationships:
        - moderator:   The staff member who performed the action
        - target_user: The user who was warned/suspended/banned
        - report:      The content report that triggered this action (optional)
    """

    __tablename__ = "moderation_actions"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The moderator/admin who took the action.
    moderator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # The user being acted upon.
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # Type of action: "warn", "suspend", or "ban". Validated by Pydantic
    # schema (pattern=r"^(warn|suspend|ban)$") before reaching the database.
    action_type: Mapped[str] = mapped_column(String(20))  # warn/suspend/ban

    # Free-text explanation of why the action was taken. Useful for appeals
    # and admin review.
    reason: Mapped[str] = mapped_column(Text)

    # For suspensions: how many hours the suspension lasts. NULL for warns
    # and bans (warns have no duration; bans are permanent). Capped at 8760
    # hours (1 year) by Pydantic validation.
    duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Optional link to the report that prompted this action. SET NULL on delete:
    # if the report is later cleaned up, the moderation action record persists.
    # Not every moderation action comes from a report (admins can act proactively).
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_reports.id", ondelete="SET NULL"), nullable=True
    )

    # Two relationships to User — disambiguated by foreign_keys.
    moderator = relationship("User", foreign_keys=[moderator_id])
    target_user = relationship("User", foreign_keys=[target_user_id])
    report = relationship("ContentReport")


class CategoryModerator(TimestampMixin, Base):
    """
    Junction table assigning moderators to specific categories.

    Database table: "category_moderators"

    PURPOSE:
        Not all moderators can moderate all categories. This table implements
        SCOPED MODERATION — a moderator is assigned to specific categories
        (e.g., "alice moderates Backend Engineering and DevOps"). This is
        similar to Reddit's per-subreddit moderator model.

    WHY A JUNCTION TABLE?
        This is a many-to-many relationship:
          - A user can moderate multiple categories.
          - A category can have multiple moderators.
        The junction table with a UniqueConstraint ensures no duplicate
        assignments.

    Relationships:
        - user:     The moderator (must have role=moderator or role=admin)
        - category: The category they moderate
    """

    __tablename__ = "category_moderators"
    __table_args__ = (
        # Prevent assigning the same moderator to the same category twice.
        UniqueConstraint("user_id", "category_id", name="uq_category_moderator"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE")
    )

    user = relationship("User")
    category = relationship("Category")


class CategoryRequest(TimestampMixin, Base):
    """
    A request from a user/moderator to create a new category (community).

    Database table: "category_requests"

    WORKFLOW:
        1. A moderator submits a request with a proposed title, slug, and
           description for a new category.
        2. The request starts with status="pending".
        3. An admin reviews the request and either:
           a. APPROVES it: creates the actual Category and sets status="approved".
           b. REJECTS it: sets status="rejected" with the reviewer recorded.

    WHY NOT LET MODERATORS CREATE CATEGORIES DIRECTLY?
        This is a design choice about governance. Categories are high-level
        organizational units that affect the entire community's structure.
        Requiring admin approval prevents category sprawl (too many niche
        categories that fragment the community).

    Relationships:
        - requester: The user who submitted the request
        - reviewer:  The admin who approved/rejected it (NULL while pending)
    """

    __tablename__ = "category_requests"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The user requesting the new category. CASCADE: if the requester is
    # deleted, their pending requests are cleaned up.
    requester_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )

    # Proposed category details — same fields as Category.
    title: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")

    # Request status. Follows the same pending -> approved/rejected pattern
    # as ContentReport's status.
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending / approved / rejected

    # The admin who reviewed the request. SET NULL preserves the request record
    # even if the admin's account is later deleted.
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # When the request was reviewed. NULL means still pending.
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Two relationships to User — disambiguated by foreign_keys since both
    # requester_id and reviewed_by reference users.id.
    requester = relationship("User", foreign_keys=[requester_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
