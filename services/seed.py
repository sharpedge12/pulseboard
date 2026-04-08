"""
Comprehensive seed script for PulseBoard.

Populates the database with realistic dummy data for demo / showcase purposes.

INTERVIEW TALKING POINTS
-------------------------
This script demonstrates several important software engineering concepts:

1. **Idempotent design** -- The script checks whether seeding has already been
   performed (by looking for an existing admin user) before inserting anything.
   This means running ``python services/seed.py`` multiple times is safe; it
   will not create duplicate data.  Idempotency is critical for any script that
   touches a database, especially in CI/CD pipelines or Docker entrypoints.

2. **Flexible database targeting** -- The script can seed either SQLite (for
   local development without Docker) or PostgreSQL (for production-like
   environments).  It checks ``--sqlite`` CLI flag and the
   ``DATABASE_URL_OVERRIDE`` environment variable to determine which database
   engine to use.

3. **Reproducible randomness** -- ``random.seed(42)`` ensures the same random
   data is generated every run.  This is useful for debugging and for writing
   assertions in tests that depend on seeded data.  The constant 42 is a
   convention (Hitchhiker's Guide reference) -- any fixed integer works.

4. **Data relationship integrity** -- The script creates data in
   dependency-order: users first, then categories, tags, threads, posts, etc.
   ``db.flush()`` is called after each batch to force the ORM to assign
   auto-increment primary keys (``id`` columns) that later rows reference as
   foreign keys.  The final ``db.commit()`` makes everything permanent in a
   single transaction -- if any step fails, ``db.rollback()`` in the except
   block ensures we don't leave partial data.

5. **Realistic data distribution** -- Votes use weighted random sampling (85%
   upvote / 15% downvote) to simulate real community engagement patterns.
   Timestamps are spread over the last 30 days to make the feed look
   naturally aged.  Different entity types (threads, posts, chat messages)
   have different volume ratios mirroring real forum activity.

Usage
-----
# Local development (uses SQLite automatically):
    python services/seed.py

# Explicit SQLite:
    python services/seed.py --sqlite

# With Docker Compose (seeds PostgreSQL):
    docker compose exec core python /shared/../seed.py
    # Or set the env var:
    DATABASE_URL_OVERRIDE="postgresql+psycopg://user:pass@localhost:5432/db" python services/seed.py

The script is IDEMPOTENT -- it checks whether the admin user already exists
and exits early if so, to avoid duplicate data on repeated runs.

To reset and re-seed, drop the database first (or ``rm -f seed_data.db`` for
SQLite) and run again.

Seeded accounts (all passwords are ``password123``):
+-----------+---------------------------+-----------+
| Username  | Email                     | Role      |
+-----------+---------------------------+-----------+
| admin     | admin@pulseboard.app      | ADMIN     |
| modmax    | modmax@pulseboard.app     | MODERATOR |
| modsara   | modsara@pulseboard.app    | MODERATOR |
| alice     | alice@pulseboard.app      | MEMBER    |
| bob       | bob@pulseboard.app        | MEMBER    |
| charlie   | charlie@pulseboard.app    | MEMBER    |
| diana     | diana@pulseboard.app      | MEMBER    |
| evan      | evan@pulseboard.app       | MEMBER    |
| fiona     | fiona@pulseboard.app      | MEMBER    |
| george    | george@pulseboard.app     | MEMBER    |
| hannah    | hannah@pulseboard.app     | MEMBER    |
| ivan      | ivan@pulseboard.app       | MEMBER    |
| julia     | julia@pulseboard.app      | MEMBER    |
| kyle      | kyle@pulseboard.app       | MEMBER    |
| luna      | luna@pulseboard.app       | MEMBER    |
| pulse     | pulse-bot@pulseboard.app  | MEMBER    |
+-----------+---------------------------+-----------+

Data volume summary:
  16 users, 8 categories, 20 tags, 22 threads, ~138 posts, ~769 votes,
  ~129 reactions, 18 friend requests, 5 chat rooms, ~57 messages,
  5 content reports, 2 moderation actions, 4 category requests,
  15 notifications, 30 audit log entries.
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# PATH SETUP: Make sure the shared package is importable regardless of
# where we invoke the script from (project root, services/, or Docker).
#
# Why this matters: Python resolves imports relative to sys.path entries.
# When you run ``python services/seed.py``, only the directory containing
# seed.py (i.e. ``services/``) is on sys.path by default.  We need the
# project root (for top-level imports) and the ``services/shared/``
# directory (for the ``shared`` package) to be importable too.
#
# ``Path(__file__).resolve()`` gives us the absolute path to this file,
# even if it was invoked via a symlink.  ``.parent`` walks up the tree.
# ---------------------------------------------------------------------------
_project_root = Path(__file__).resolve().parent.parent  # e.g. /app or repo root
_services_dir = Path(__file__).resolve().parent  # e.g. /app/services
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_services_dir))
sys.path.insert(0, str(_services_dir / "shared"))  # allows ``from shared.xxx``

# ---------------------------------------------------------------------------
# DATABASE URL SELECTION
# ---------------------------------------------------------------------------
# This block decides which database to seed:
#
# 1. If ``--sqlite`` is passed on the command line, use a local SQLite file
#    at the project root (``seed_data.db``).  Great for quick local demos.
#
# 2. If ``DATABASE_URL_OVERRIDE`` is already set in the environment (e.g.
#    by Docker Compose or the user), use that.  This is how you seed the
#    PostgreSQL database in a containerised setup.
#
# 3. If neither is set (most common for local dev), default to SQLite so
#    the script works out-of-the-box without requiring PostgreSQL/Redis.
#
# The ``os.environ.setdefault`` call only writes the env var if it isn't
# already present, so an explicit ``DATABASE_URL_OVERRIDE=postgres://...``
# always wins.
# ---------------------------------------------------------------------------
if "--sqlite" in sys.argv or not os.environ.get("DATABASE_URL_OVERRIDE"):
    _sqlite_path = _project_root / "seed_data.db"
    os.environ.setdefault(
        "DATABASE_URL_OVERRIDE",
        f"sqlite:///{_sqlite_path}",
    )

# ---------------------------------------------------------------------------
# ORM and model imports.  These are placed after the env-var setup above
# because ``shared.core.database`` reads ``DATABASE_URL_OVERRIDE`` at import
# time to configure the SQLAlchemy engine.  ``# noqa: E402`` suppresses the
# flake8 "module level import not at top of file" warning -- unavoidable here
# because we need the env var set first.
# ---------------------------------------------------------------------------
from shared.core.database import SessionLocal, init_db  # noqa: E402
from shared.core.security import hash_password  # noqa: E402
from shared.models import (  # noqa: E402
    AuditLog,
    Category,
    CategoryModerator,
    CategoryRequest,
    ChatRoom,
    ChatRoomMember,
    ContentReport,
    FriendRequest,
    FriendRequestStatus,
    Message,
    ModerationAction,
    Notification,
    Post,
    Reaction,
    Tag,
    Thread,
    ThreadSubscription,
    ThreadTag,
    User,
    UserRole,
    Vote,
)

# ---------------------------------------------------------------------------
# Reproducible randomness
# ---------------------------------------------------------------------------
# Setting a fixed seed means every invocation generates identical "random"
# data.  This is essential for:
#   - Consistent screenshots / demos
#   - Deterministic test expectations
#   - Debugging ("the vote count should be exactly 769 every time")
#
# The number 42 is a common convention (from "The Hitchhiker's Guide to the
# Galaxy").  Any constant integer produces the same reproducibility guarantee.
# ---------------------------------------------------------------------------
random.seed(42)

# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------
# All seeded data is anchored to ``_NOW`` (the time the script runs).
# Two helpers produce random timestamps relative to ``_NOW``:
#
# - ``_past()`` -- a random moment in the last N days.  Used for threads,
#   posts, friend requests, and other "historical" data that should look
#   like it accumulated over time.
#
# - ``_recent()`` -- a random moment in the last N hours.  Used for
#   ``last_seen`` timestamps on users so they appear recently active.
#
# Storing timezone-aware UTC datetimes (``timezone.utc``) is a best practice.
# It avoids ambiguity when the server and database are in different time zones.
# ---------------------------------------------------------------------------
_NOW = datetime.now(timezone.utc)


def _past(days_ago_max: int = 30) -> datetime:
    """Return a random UTC timestamp within the last *days_ago_max* days.

    Used to give threads, posts, and other historical records a natural
    spread of creation dates so the seeded forum doesn't look like
    everything was posted at the same instant.

    Args:
        days_ago_max: The maximum number of days in the past the timestamp
            can be.  A value of 30 means the returned datetime will be
            somewhere between "right now" and "30 days ago".

    Returns:
        A timezone-aware ``datetime`` object in UTC.

    Example:
        >>> ts = _past(7)   # random moment in the last week
    """
    delta = timedelta(
        days=random.randint(0, days_ago_max),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return _NOW - delta


def _recent(hours_max: int = 48) -> datetime:
    """Return a random UTC timestamp within the last *hours_max* hours.

    Used primarily for ``User.last_seen`` so that seeded users appear to
    have been active recently (within the "online" threshold of 5 minutes
    for some, within 72 hours for others).

    Args:
        hours_max: The upper bound for how many hours ago the timestamp
            can be.

    Returns:
        A timezone-aware ``datetime`` object in UTC.
    """
    delta = timedelta(
        hours=random.randint(0, hours_max),
        minutes=random.randint(0, 59),
    )
    return _NOW - delta


# ---------------------------------------------------------------------------
# Shared demo password
# ---------------------------------------------------------------------------
# Every seeded user account gets the same password so that developers and
# reviewers can log in as any user during demos.  The password is hashed
# once here (using pbkdf2_sha256 via passlib) and reused for all 16 users
# rather than hashing 16 times -- a minor but nice optimisation.
# ---------------------------------------------------------------------------
DEMO_PASSWORD = "password123"
DEMO_HASH = hash_password(DEMO_PASSWORD)

# ===== DATA =================================================================
# All seed data is defined as plain Python constants (lists of tuples).
# This keeps the data declarative and easy to review.  The ``seed()``
# function below iterates over these constants and converts them into
# SQLAlchemy model instances.
# ============================================================================

# -- Categories ---------------------------------------------------------------
# 8 categories that mirror typical developer community sections.
# Format: (display_title, url_slug, description)
#
# The slug is used in URL paths (e.g. ``/api/v1/categories/backend``) and as
# a lookup key in the THREADS_DATA below.  It must be unique per category.
#
# NOTE: The application's ``init_db()`` may create some of these categories
# automatically on startup.  The seed function handles this gracefully by
# checking for existing slugs before inserting (see "category reuse logic"
# in the ``seed()`` function).
# -----------------------------------------------------------------------------
CATEGORIES_DATA = [
    (
        "General Discussion",  # Catch-all for broad topics
        "general",
        "Project updates, questions, and broad discussion.",
    ),
    (
        "Backend Engineering",  # API, databases, server-side
        "backend",
        "API design, FastAPI, databases, and infrastructure.",
    ),
    (
        "Frontend Engineering",  # UI, UX, React, CSS
        "frontend",
        "React UI, UX, and integration work.",
    ),
    (
        "DevOps and Deployment",  # Docker, CI/CD, cloud
        "devops",
        "Docker, Redis, Render, Vercel, and deployment notes.",
    ),
    (
        "Show and Tell",  # Project showcases
        "showandtell",
        "Share your projects, demos, or cool things you built.",
    ),
    (
        "Feedback and Suggestions",  # Platform improvement ideas
        "feedback",
        "Ideas and suggestions for improving PulseBoard.",
    ),
    (
        "Off-Topic",  # Casual / non-technical chatter
        "offtopic",
        "Anything that doesn't fit elsewhere.",
    ),
    (
        "Help and Support",  # Q&A for setup, bugs, usage
        "help",
        "Ask for help with bugs, setup, or usage questions.",
    ),
]

# -- Tags ---------------------------------------------------------------------
# 20 tags that can be attached to threads for discoverability and filtering.
# These cover the main technology areas discussed on the platform.
#
# Tags are many-to-many with threads via the ``ThreadTag`` join table.
# A thread can have up to 10 tags (enforced by schema validation elsewhere).
# -----------------------------------------------------------------------------
TAGS_DATA = [
    "python",  # Core backend language
    "fastapi",  # Web framework used by PulseBoard
    "react",  # Frontend UI library
    "docker",  # Containerisation
    "postgresql",  # Primary production database
    "redis",  # Pub/sub and caching
    "javascript",  # Frontend scripting
    "css",  # Styling
    "websocket",  # Real-time communication
    "jwt",  # JSON Web Token auth
    "oauth",  # Third-party auth (Google, GitHub)
    "testing",  # Test strategies and tooling
    "performance",  # Optimisation topics
    "security",  # AppSec, hardening
    "deployment",  # Shipping to production
    "beginner",  # Newcomer-friendly content
    "discussion",  # Open-ended conversations
    "bug",  # Bug reports
    "feature-request",  # Enhancement proposals
    "tutorial",  # Step-by-step guides
]

# -- Users --------------------------------------------------------------------
# 16 user accounts representing a realistic community:
#   - 1 admin      -- full platform control (user management, all mod powers)
#   - 2 moderators -- can resolve reports, warn/ban users, manage categories
#   - 12 members   -- regular community participants with varied expertise
#   - 1 bot        -- the @pulse AI assistant (auto-replies when mentioned)
#
# Format: (username, email, role_enum, bio_text)
#
# Every account has ``is_verified=True`` and ``is_active=True`` so they can
# log in immediately.  In production, users must verify their email first.
#
# The bios are written to feel like real developer profiles and showcase the
# diversity of a healthy community (frontend, backend, data science, DevOps,
# junior devs, senior leads, designers, security researchers, etc.).
# -----------------------------------------------------------------------------
USERS_DATA = [
    # (username, email, role, bio)
    (
        "admin",  # Platform administrator
        "admin@pulseboard.app",
        UserRole.ADMIN,
        "PulseBoard administrator. I keep the lights on.",
    ),
    (
        "modmax",  # Moderator 1 of 2
        "modmax@pulseboard.app",
        UserRole.MODERATOR,
        "Community moderator. Happy to help maintain quality discussions.",
    ),
    (
        "modsara",  # Moderator 2 of 2
        "modsara@pulseboard.app",
        UserRole.MODERATOR,
        "Moderator and backend enthusiast. FastAPI fan.",
    ),
    (
        "alice",  # Full-stack developer
        "alice@pulseboard.app",
        UserRole.MEMBER,
        "Full-stack developer. Love React and Python equally.",
    ),
    (
        "bob",  # DevOps engineer
        "bob@pulseboard.app",
        UserRole.MEMBER,
        "DevOps engineer. Docker, Kubernetes, and CI/CD pipelines.",
    ),
    (
        "charlie",  # Frontend / CSS specialist
        "charlie@pulseboard.app",
        UserRole.MEMBER,
        "Frontend wizard. CSS is my superpower.",
    ),
    (
        "diana",  # Data scientist, new to web dev
        "diana@pulseboard.app",
        UserRole.MEMBER,
        "Data scientist exploring web dev. New to FastAPI.",
    ),
    (
        "evan",  # Junior developer
        "evan@pulseboard.app",
        UserRole.MEMBER,
        "Junior developer learning the ropes. Eager to contribute!",
    ),
    (
        "fiona",  # Security researcher
        "fiona@pulseboard.app",
        UserRole.MEMBER,
        "Security researcher. Always thinking about edge cases.",
    ),
    (
        "george",  # Tech lead / architect
        "george@pulseboard.app",
        UserRole.MEMBER,
        "Tech lead at a startup. Interested in microservices.",
    ),
    (
        "hannah",  # UX designer who codes
        "hannah@pulseboard.app",
        UserRole.MEMBER,
        "UX designer who codes. Bridging design and engineering.",
    ),
    (
        "ivan",  # OSS contributor, Rust + Python
        "ivan@pulseboard.app",
        UserRole.MEMBER,
        "Open-source contributor. Rust and Python are my go-to languages.",
    ),
    (
        "julia",  # Mobile dev branching into web
        "julia@pulseboard.app",
        UserRole.MEMBER,
        "Mobile developer branching into web. React Native -> React.",
    ),
    (
        "kyle",  # Database specialist
        "kyle@pulseboard.app",
        UserRole.MEMBER,
        "Database nerd. PostgreSQL, SQLite, and query optimization.",
    ),
    (
        "luna",  # Cloud architect
        "luna@pulseboard.app",
        UserRole.MEMBER,
        "Cloud architect. AWS, GCP, and infrastructure as code.",
    ),
    (
        "pulse",  # AI bot (@pulse)
        "pulse-bot@pulseboard.app",
        UserRole.MEMBER,  # Bot has MEMBER role, not ADMIN
        "I'm Pulse, the PulseBoard AI assistant. Mention me with @pulse!",
    ),
]

# -- Threads and Posts --------------------------------------------------------
# The heart of the seed data.  Each entry defines a complete thread with its
# nested reply tree.
#
# Format: (category_slug, author_username, title, body, [tag_names], [posts])
#   - ``category_slug`` links to CATEGORIES_DATA above
#   - ``author_username`` must exist in USERS_DATA
#   - ``[tag_names]`` are names from TAGS_DATA to attach via ThreadTag
#   - ``[posts]`` is a list of top-level replies to the thread
#
# Each post is: (author_username, body, [replies])
#   - ``[replies]`` is a list of nested replies (children of that post):
#     each reply is (author_username, body)
#
# This recursive structure mirrors how the Reddit-style comment tree works:
#   Thread
#     -> Post (top-level reply, parent_post_id=None)
#         -> Reply (nested reply, parent_post_id=post.id)
#         -> Reply
#     -> Post
#         -> Reply
#
# 22 threads total, distributed across all 8 categories to make the forum
# feel populated.  Thread content is realistic developer discussion to
# demonstrate the platform's features during demos.
# -----------------------------------------------------------------------------
THREADS_DATA = [
    # =========================================================================
    # GENERAL DISCUSSION (3 threads)
    # =========================================================================
    # Thread 1: Welcome thread -- will be PINNED after creation (see step 13)
    (
        "general",
        "admin",
        "Welcome to PulseBoard!",
        "Hey everyone! Welcome to PulseBoard, our new community discussion platform. "
        "This is the place to share ideas, ask questions, and connect with fellow developers.\n\n"
        "A few ground rules:\n"
        "1. Be respectful and constructive\n"
        "2. Use the right category for your posts\n"
        "3. Tag your threads for better discoverability\n"
        "4. Have fun!\n\n"
        "Feel free to introduce yourself in the replies.",
        ["discussion"],  # Tags for this thread
        [
            # -- Top-level post 1: Alice introduces herself --
            (
                "alice",
                "Excited to be here! I'm Alice, a full-stack dev working with React and Python. Looking forward to great discussions.",
                [
                    # Nested reply from Bob (parent_post_id = alice's post id)
                    (
                        "bob",
                        "Welcome Alice! Fellow Python enthusiast here. What frameworks do you use?",
                    ),
                    # Alice replies back (same parent_post_id)
                    (
                        "alice",
                        "Mainly FastAPI for the backend and React with Vite on the frontend. You?",
                    ),
                    # Charlie also replies
                    (
                        "charlie",
                        "React gang! I'm more on the CSS side of things though.",
                    ),
                ],
            ),
            # -- Top-level post 2: Evan asks about the platform --
            (
                "evan",
                "Hi everyone! I'm Evan, a junior dev. This platform looks amazing. Who built it?",
                [
                    (
                        "admin",
                        "The PulseBoard team built it! It's a FastAPI + React stack with PostgreSQL and Redis.",
                    ),
                    (
                        "evan",
                        "That's awesome. I'd love to learn more about the architecture.",
                    ),
                ],
            ),
            # -- Top-level post 3: Diana (no nested replies) --
            (
                "diana",
                "Hello from the data science world! Hoping to learn more about web development here.",
                [],  # Empty replies list = no nested children
            ),
            # -- Top-level post 4: George praises real-time features --
            (
                "george",
                "Great initiative! The real-time features with WebSocket are impressive.",
                [
                    (
                        "modmax",
                        "Thanks George! Redis pub/sub powers the real-time bridge. Feel free to dig into the code.",
                    ),
                ],
            ),
            # -- Top-level post 5: Hannah compliments the design --
            (
                "hannah",
                "Love the Reddit-inspired design. Clean and familiar. Nice work!",
                [],
            ),
        ],
    ),
    # Thread 2: Code review best practices (community knowledge-sharing)
    (
        "general",
        "george",
        "Best practices for code reviews",
        "I've been thinking about how to improve our code review process. "
        "What are your best practices?\n\n"
        "Some things I've found helpful:\n"
        "- Review for correctness first, style second\n"
        "- Keep PRs small and focused\n"
        "- Use checklists for common issues\n"
        "- Be kind but thorough in feedback",
        ["discussion"],
        [
            (
                "fiona",
                "Great topic! I'd add: always check for security implications. SQL injection, XSS, auth bypass -- these are easy to miss in reviews.",
                [
                    (
                        "george",
                        "Absolutely. Security should be a first-class concern in every review.",
                    ),
                    (
                        "kyle",
                        "Parameterized queries should be non-negotiable. SQLAlchemy handles this well.",
                    ),
                ],
            ),
            (
                "alice",
                "I like the 'two hats' approach: first review for logic, then review for style. Mixing them leads to unfocused feedback.",
                [],
            ),
            (
                "modmax",
                "Automated linting (Black + Ruff) eliminates most style debates. Saves so much review time.",
                [
                    ("ivan", "+1 for Ruff. It's incredibly fast and catches a lot."),
                ],
            ),
            (
                "evan",
                "As a junior dev, I appreciate when reviewers explain *why* something should change, not just *what* to change.",
                [
                    (
                        "hannah",
                        "This is so important. Reviews are a learning opportunity for everyone involved.",
                    ),
                    (
                        "george",
                        "Agreed 100%. We should always aim to teach, not just correct.",
                    ),
                ],
            ),
        ],
    ),
    # Thread 3: Community Guidelines -- will be PINNED + LOCKED after creation
    (
        "general",
        "modmax",
        "Community Guidelines - Please Read",
        "Quick reminder of our community guidelines:\n\n"
        "**Do:**\n"
        "- Ask questions, no matter how basic\n"
        "- Share your projects and learnings\n"
        "- Help others with constructive answers\n"
        "- Report content that violates guidelines\n\n"
        "**Don't:**\n"
        "- Post spam or self-promotional content\n"
        "- Harass or insult other members\n"
        "- Share copyrighted material without permission\n"
        "- Post NSFW content\n\n"
        "Violations will be handled by the mod team. Thanks for keeping PulseBoard awesome!",
        ["discussion"],
        [
            ("alice", "Pinned and bookmarked. Thanks for laying this out clearly!", []),
            (
                "evan",
                "Good to know the expectations. Happy to be part of a well-moderated community.",
                [],
            ),
        ],
    ),
    # =========================================================================
    # BACKEND ENGINEERING (4 threads)
    # =========================================================================
    # Thread 4: FastAPI dependency injection (technical deep-dive)
    (
        "backend",
        "alice",
        "FastAPI dependency injection patterns",
        "I've been exploring different patterns for dependency injection in FastAPI. "
        "The `Depends()` system is powerful but can get complex.\n\n"
        "Here's a pattern I like for database sessions:\n\n"
        "```python\ndef get_db():\n    db = SessionLocal()\n    try:\n        yield db\n    finally:\n        db.close()\n```\n\n"
        "For auth, I chain dependencies:\n"
        "```python\ndef get_current_user(token = Depends(oauth2_scheme), db = Depends(get_db)):\n    ...\n```\n\n"
        "What patterns do you use?",
        ["python", "fastapi"],  # Two tags for cross-topic discoverability
        [
            (
                "george",
                "Nice writeup! I use a similar pattern but with async sessions for better concurrency:\n\n"
                "```python\nasync def get_async_db():\n    async with async_session() as session:\n        yield session\n```",
                [
                    (
                        "alice",
                        "Good point about async. We're using sync SQLAlchemy here but async would be better for high-concurrency scenarios.",
                    ),
                    (
                        "kyle",
                        "Just be careful with async sessions and connection pooling. The defaults might not be optimal.",
                    ),
                ],
            ),
            (
                "fiona",
                "For auth dependencies, consider adding rate limiting as a dependency too. It composes nicely:\n\n"
                "```python\ndef rate_limit(request: Request, limit: int = 100):\n    # Check rate limit\n    ...\n```",
                [
                    (
                        "alice",
                        "That's a great idea. Dependencies as middleware-like components.",
                    ),
                ],
            ),
            (
                "modsara",
                "I prefer putting complex business logic in service classes and keeping routes thin. Dependencies wire everything together.",
                [
                    (
                        "george",
                        "Same. Fat models, thin routes. Service layer handles the complexity.",
                    ),
                ],
            ),
        ],
    ),
    # Thread 5: PostgreSQL query optimization (database performance)
    (
        "backend",
        "kyle",
        "PostgreSQL query optimization tips",
        "Been profiling some slow queries in our app. Here are some tips I've gathered:\n\n"
        "1. **Use EXPLAIN ANALYZE** to see actual execution plans\n"
        "2. **Index foreign keys** - SQLAlchemy doesn't do this by default\n"
        "3. **Avoid N+1 queries** - use `joinedload()` or `selectinload()`\n"
        "4. **Use partial indexes** for filtered queries\n"
        "5. **VACUUM regularly** in production\n\n"
        "The biggest win for us was adding a composite index on `(entity_type, entity_id)` for the votes table. "
        "Went from 200ms to 3ms for vote counts.",
        ["postgresql", "performance"],
        [
            (
                "george",
                "Great tips! The N+1 problem is the #1 performance killer in ORMs. SQLAlchemy's lazy loading is a trap for the unwary.",
                [
                    (
                        "kyle",
                        "Exactly. I always set `lazy='raise'` on relationships in production to catch N+1 issues early.",
                    ),
                    (
                        "alice",
                        "TIL about `lazy='raise'`. That's a game-changer for debugging.",
                    ),
                ],
            ),
            (
                "ivan",
                "For large tables, consider using `BRIN` indexes instead of `B-tree` for timestamp columns. Much smaller and nearly as fast for range queries.",
                [
                    (
                        "kyle",
                        "Good call. BRIN is perfect for append-only tables like audit_logs.",
                    ),
                ],
            ),
            (
                "diana",
                "As someone coming from data science, I appreciate the EXPLAIN ANALYZE tip. SQL performance tuning is a whole different world.",
                [],
            ),
            (
                "bob",
                "Connection pooling is another big one. PgBouncer in transaction mode saved us a lot of headaches.",
                [
                    (
                        "kyle",
                        "Absolutely. And make sure your SQLAlchemy pool settings match your PgBouncer config.",
                    ),
                ],
            ),
        ],
    ),
    # Thread 6: JWT token rotation (security + auth design)
    (
        "backend",
        "modsara",
        "JWT token rotation strategies",
        "Let's discuss JWT token management strategies. Currently PulseBoard uses:\n"
        "- Access tokens (30 min expiry)\n"
        "- Refresh tokens (7 days, stored in DB)\n\n"
        "Some questions:\n"
        "1. Should we implement token rotation on refresh?\n"
        "2. How do you handle token revocation at scale?\n"
        "3. Is there a better approach than storing refresh tokens in the DB?",
        ["jwt", "security"],
        [
            (
                "fiona",
                "Token rotation on refresh is a must for security. If a refresh token is stolen, rotation limits the window of abuse.\n\n"
                "The flow: on refresh, issue a new refresh token AND invalidate the old one. If the old one is used again, revoke ALL tokens for that user (detected reuse = compromise).",
                [
                    (
                        "modsara",
                        "That's the approach I was leaning toward. The 'reuse detection' part is clever.",
                    ),
                    (
                        "george",
                        "We implemented this at my company. Redis TTL keys work great for tracking token families.",
                    ),
                ],
            ),
            (
                "alice",
                "For revocation at scale, a Redis blocklist with TTL matching the access token expiry is efficient. No need to check the DB on every request.",
                [
                    (
                        "kyle",
                        "Agreed. Redis is perfect for this. SET with EX flag, check on auth middleware.",
                    ),
                ],
            ),
            (
                "ivan",
                "Have you considered using opaque tokens instead of JWTs for refresh tokens? They're simpler and inherently revocable since you always hit the DB.",
                [],
            ),
        ],
    ),
    # Thread 7: File upload security (applied security engineering)
    (
        "backend",
        "fiona",
        "Securing file upload endpoints",
        "File uploads are one of the trickiest things to secure. Here's my checklist:\n\n"
        "- Validate MIME types server-side (don't trust Content-Type headers)\n"
        "- Check file magic bytes for actual file type\n"
        "- Limit file size (both per-file and per-request)\n"
        "- Generate random filenames (never use user-supplied names)\n"
        "- Store uploads outside the web root\n"
        "- Scan for malware if handling user content at scale\n"
        "- Set Content-Disposition: attachment for downloads\n\n"
        "PulseBoard currently handles avatar uploads. Are there other upload scenarios we should plan for?",
        ["security", "fastapi"],
        [
            (
                "bob",
                "Attachment uploads for thread posts would be useful. Images, code snippets as files, maybe PDFs.",
                [
                    (
                        "fiona",
                        "Good call. We'd need a separate storage path and stricter validation for those.",
                    ),
                ],
            ),
            (
                "charlie",
                "From a frontend perspective, drag-and-drop upload with progress bars would be great UX.",
                [
                    (
                        "hannah",
                        "Agreed! And image previews before upload. The current avatar upload is functional but basic.",
                    ),
                ],
            ),
            (
                "admin",
                "We're planning to add post attachments in the next sprint. This checklist will be our guide. Thanks Fiona!",
                [],
            ),
        ],
    ),
    # =========================================================================
    # FRONTEND ENGINEERING (3 threads)
    # =========================================================================
    # Thread 8: CSS custom properties vs CSS-in-JS (design systems debate)
    (
        "frontend",
        "charlie",
        "CSS custom properties vs CSS-in-JS",
        "PulseBoard uses CSS custom properties (design tokens) for theming. "
        "I think this is the right call over CSS-in-JS. Here's why:\n\n"
        "**CSS Custom Properties:**\n"
        "- Zero runtime cost\n"
        "- Native browser feature\n"
        "- Easy theme switching with `data-theme` attribute\n"
        "- Great DevTools support\n\n"
        "**CSS-in-JS (styled-components, etc):**\n"
        "- Runtime overhead (parsing, injecting)\n"
        "- Larger bundle size\n"
        "- Better TypeScript integration\n"
        "- Component-scoped by default\n\n"
        "For a Reddit-style forum, plain CSS with custom properties is the sweet spot.",
        ["css", "react", "discussion"],  # 3 tags: cross-cutting topic
        [
            (
                "hannah",
                "Totally agree. The theming in PulseBoard is proof that you don't need CSS-in-JS for a good design system. The dark/light toggle is seamless.",
                [
                    (
                        "charlie",
                        "Thanks! The `[data-theme='light']` selector on `:root` makes it trivially easy to override tokens.",
                    ),
                ],
            ),
            (
                "alice",
                "I used to be a styled-components fan, but the runtime cost is real. On lower-end devices, you can feel the difference.",
                [
                    (
                        "george",
                        "Same experience. We switched from styled-components to CSS Modules and saw a 15% improvement in First Contentful Paint.",
                    ),
                ],
            ),
            (
                "julia",
                "Coming from React Native where StyleSheet is the norm, I appreciate the simplicity of plain CSS. Less abstraction, more control.",
                [],
            ),
            (
                "evan",
                "As a beginner, I find plain CSS much easier to debug. The browser DevTools just work.",
                [
                    (
                        "charlie",
                        "That's a huge advantage. No source maps to configure, no runtime layer to debug through.",
                    ),
                ],
            ),
        ],
    ),
    # Thread 9: Accessibility in web forums
    (
        "frontend",
        "hannah",
        "Accessible design patterns for forums",
        "Accessibility is often an afterthought in web forums. Let's fix that.\n\n"
        "Key areas for PulseBoard:\n"
        "1. **Keyboard navigation** - Can users navigate threads without a mouse?\n"
        "2. **Screen reader support** - Are ARIA labels in place?\n"
        "3. **Color contrast** - Does the dark theme meet WCAG AA?\n"
        "4. **Focus management** - After posting a reply, where does focus go?\n"
        "5. **Semantic HTML** - Are we using `<article>`, `<nav>`, `<main>` correctly?\n\n"
        "I've done a quick audit and have some suggestions.",
        ["css", "discussion"],
        [
            (
                "charlie",
                "Great initiative! I know our color contrast could be better in a few places. The muted text on dark backgrounds is borderline.",
                [
                    (
                        "hannah",
                        "Exactly. I measured some of them at 3.8:1 ratio. WCAG AA requires 4.5:1 for normal text. Easy fix by bumping the brightness.",
                    ),
                ],
            ),
            (
                "alice",
                "The keyboard shortcuts we have (Enter to send, Ctrl+Enter to save) are good. But we need visible focus indicators too.",
                [
                    (
                        "hannah",
                        "Yes! The `:focus-visible` pseudo-class is our friend. Visible for keyboard, hidden for mouse clicks.",
                    ),
                ],
            ),
            (
                "evan",
                "I've been learning about accessibility. This is super valuable. Are there any automated tools you recommend?",
                [
                    (
                        "hannah",
                        "axe DevTools (browser extension) is excellent for automated checks. Lighthouse too, but axe is more thorough.",
                    ),
                    (
                        "charlie",
                        "Also check out the WAVE tool. It gives a visual overlay of accessibility issues.",
                    ),
                ],
            ),
        ],
    ),
    # Thread 10: React 18 concurrent features
    (
        "frontend",
        "julia",
        "React 18 concurrent features in production",
        "Has anyone used React 18's concurrent features in a real app?\n\n"
        "I'm curious about:\n"
        "- `useTransition` for non-urgent state updates\n"
        "- `useDeferredValue` for expensive renders\n"
        "- Suspense for data fetching\n\n"
        "PulseBoard uses React 18 -- are any of these being used?",
        ["react", "javascript", "performance"],
        [
            (
                "alice",
                "We're not using concurrent features yet. The app is fast enough with traditional patterns. But `useTransition` could help with the thread search.",
                [
                    (
                        "julia",
                        "That's what I was thinking. Search input + filtering a long thread list seems like the perfect use case.",
                    ),
                ],
            ),
            (
                "george",
                "We use `useDeferredValue` at my company for a large data table. It made scroll performance much smoother. The deferred value lags behind by one frame.",
                [
                    (
                        "charlie",
                        "Interesting. I've been hesitant to adopt it because the mental model is a bit different from debouncing.",
                    ),
                ],
            ),
            (
                "ivan",
                "Suspense for data fetching is still experimental in React 18. I'd wait for React 19 or use TanStack Query which has its own Suspense integration.",
                [],
            ),
        ],
    ),
    # =========================================================================
    # DEVOPS AND DEPLOYMENT (3 threads)
    # =========================================================================
    # Thread 11: Docker multi-stage builds
    (
        "devops",
        "bob",
        "Docker multi-stage builds for FastAPI",
        "Here's the Dockerfile pattern I use for FastAPI services:\n\n"
        "```dockerfile\n# Stage 1: Builder\nFROM python:3.12-slim AS builder\n"
        "WORKDIR /build\nCOPY requirements.txt .\n"
        "RUN pip install --no-cache-dir --prefix=/install -r requirements.txt\n\n"
        "# Stage 2: Runtime\nFROM python:3.12-slim\n"
        "COPY --from=builder /install /usr/local\nCOPY . /app\n"
        'WORKDIR /app\nCMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]\n```\n\n'
        "This cuts the image size by ~60% compared to a single-stage build.",
        ["docker", "deployment", "python"],
        [
            (
                "luna",
                "Great pattern! I'd also add a `.dockerignore` to exclude `__pycache__`, `.git`, and `node_modules`. Every MB counts.",
                [
                    (
                        "bob",
                        "Good point. Our `.dockerignore` already handles that but it's worth mentioning.",
                    ),
                ],
            ),
            (
                "george",
                "For production, I'd add a non-root user:\n\n```dockerfile\nRUN adduser --disabled-password --gecos '' appuser\nUSER appuser\n```",
                [
                    (
                        "fiona",
                        "Security best practice. Never run as root in containers.",
                    ),
                ],
            ),
            (
                "kyle",
                "One thing I've noticed: `pip install --no-cache-dir` is crucial in Docker. The pip cache can add 100MB+ to the layer.",
                [],
            ),
            (
                "evan",
                "This is really helpful! I've been using single-stage builds. Time to refactor.",
                [],
            ),
        ],
    ),
    # Thread 12: Monitoring microservices
    (
        "devops",
        "luna",
        "Monitoring microservices with Prometheus",
        "For a microservices architecture like PulseBoard, monitoring is essential.\n\n"
        "My recommended stack:\n"
        "- **Prometheus** for metrics collection\n"
        "- **Grafana** for dashboards\n"
        "- **Loki** for log aggregation\n"
        "- **Alertmanager** for notifications\n\n"
        "Key metrics to track:\n"
        "1. Request latency (p50, p95, p99)\n"
        "2. Error rate by endpoint\n"
        "3. Database connection pool utilization\n"
        "4. Redis pub/sub lag\n"
        "5. WebSocket connection count",
        ["deployment", "performance"],
        [
            (
                "bob",
                "This is exactly what we need. I'd add container-level metrics too: CPU, memory, disk I/O per service.",
                [
                    (
                        "luna",
                        "Absolutely. cAdvisor + Prometheus handles that nicely. One sidecar container.",
                    ),
                ],
            ),
            (
                "george",
                "For FastAPI specifically, the `prometheus-fastapi-instrumentator` package is excellent. It auto-instruments all routes.",
                [
                    (
                        "alice",
                        "Used it before. It adds histogram metrics for every endpoint. Very low overhead.",
                    ),
                ],
            ),
            (
                "fiona",
                "Don't forget to monitor auth failures. A spike in 401s could indicate a credential stuffing attack.",
                [
                    (
                        "luna",
                        "Great point. We should set up Alertmanager rules for auth anomalies.",
                    ),
                ],
            ),
        ],
    ),
    # Thread 13: CI/CD with GitHub Actions
    (
        "devops",
        "bob",
        "CI/CD pipeline with GitHub Actions",
        "Sharing our GitHub Actions workflow for PulseBoard:\n\n"
        "```yaml\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Set up Python\n        uses: actions/setup-python@v5\n"
        "      - name: Install deps\n        run: pip install -e services/shared && ...\n"
        "      - name: Run tests\n        run: pytest services/tests/ -x -v\n"
        "  build:\n    needs: test\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - name: Build Docker images\n        run: docker compose build\n```\n\n"
        "What would you add to this pipeline?",
        ["deployment", "testing"],
        [
            (
                "luna",
                "I'd add a security scanning step. `trivy` for container vulnerabilities, `bandit` for Python security issues.",
                [
                    (
                        "fiona",
                        "Yes! `bandit` is great. Also consider `safety` for checking dependencies against known CVEs.",
                    ),
                ],
            ),
            (
                "alice",
                "Linting step before tests: `ruff check .` and `mypy .`. Fail fast on style/type issues.",
                [],
            ),
            (
                "kyle",
                "Database migration check would be useful too. Even though we use `create_all()`, we should verify the schema matches models.",
                [],
            ),
            (
                "ivan",
                "I'd add caching for pip dependencies. Actions cache can save 30-60 seconds per run.",
                [
                    (
                        "bob",
                        "Good call. The `actions/cache` action with a hash of requirements.txt works well.",
                    ),
                ],
            ),
        ],
    ),
    # =========================================================================
    # SHOW AND TELL (2 threads)
    # =========================================================================
    # Thread 14: CLI migration tool
    (
        "showandtell",
        "ivan",
        "Built a CLI tool for managing SQLAlchemy migrations",
        "I built a small CLI tool that generates SQL migration scripts from SQLAlchemy model diffs. "
        "It's not a replacement for Alembic but it's much simpler for small projects.\n\n"
        "Features:\n"
        "- Detects new columns, removed columns, type changes\n"
        "- Generates raw SQL ALTER TABLE statements\n"
        "- Supports PostgreSQL and SQLite\n"
        "- ~200 lines of Python\n\n"
        "Would love feedback! Thinking about open-sourcing it.",
        ["python", "postgresql", "tutorial"],
        [
            (
                "kyle",
                "This is exactly what I've wanted! Alembic is overkill for small projects. I'd definitely use this.",
                [
                    (
                        "ivan",
                        "Thanks Kyle! I'll clean it up and share the repo next week.",
                    ),
                ],
            ),
            (
                "alice",
                "Does it handle index changes too? That's one of the trickier parts of migration.",
                [
                    (
                        "ivan",
                        "Not yet, but it's on my TODO list. Index diffs are tricky because of naming conventions.",
                    ),
                ],
            ),
            (
                "modsara",
                "PulseBoard uses raw SQL migrations in `_run_migrations()`. This could replace that pattern nicely.",
                [],
            ),
        ],
    ),
    # Thread 15: Plotly + FastAPI dashboard
    (
        "showandtell",
        "diana",
        "Data visualization dashboard with Plotly + FastAPI",
        "I built a real-time data dashboard using Plotly Dash embedded in a FastAPI app.\n\n"
        "The cool part: it uses WebSocket to push live data updates to the charts. "
        "No polling, no SSE -- pure WebSocket.\n\n"
        "Tech stack:\n"
        "- FastAPI for the API layer\n"
        "- Plotly Dash for the visualizations\n"
        "- PostgreSQL for data storage\n"
        "- Redis pub/sub for real-time events (same pattern as PulseBoard!)\n\n"
        "The hardest part was integrating Dash's WSGI app inside FastAPI's ASGI server.",
        ["python", "fastapi", "websocket"],
        [
            (
                "george",
                "Mounting WSGI inside ASGI is indeed tricky. Did you use `a]2wsgi` or FastAPI's built-in mount?",
                [
                    (
                        "diana",
                        "I used `a2wsgi`. FastAPI's `WSGIMiddleware` works but `a2wsgi` handles edge cases better.",
                    ),
                ],
            ),
            (
                "luna",
                "This sounds amazing for monitoring dashboards. Could you share the WebSocket bridge pattern?",
                [
                    (
                        "diana",
                        "It's very similar to PulseBoard's Redis-to-WS bridge in the gateway. Subscribe to a Redis channel, forward messages to connected clients.",
                    ),
                ],
            ),
            (
                "evan",
                "Would love to see a tutorial on this. Plotly + FastAPI is a combo I haven't seen documented well.",
                [],
            ),
        ],
    ),
    # =========================================================================
    # FEEDBACK AND SUGGESTIONS (2 threads)
    # =========================================================================
    # Thread 16: Markdown preview feature request
    (
        "feedback",
        "hannah",
        "Suggestion: Add markdown preview for posts",
        "Currently when writing a post, you can't preview the markdown rendering. "
        "A split-pane or tabbed preview would be really helpful.\n\n"
        "Proposed UX:\n"
        "1. **Tab mode**: 'Write' and 'Preview' tabs above the textarea\n"
        "2. **Split mode**: Side-by-side on wider screens\n"
        "3. **Toolbar**: Bold, italic, code, link, image buttons\n\n"
        "This would especially help new users who aren't familiar with markdown syntax.",
        ["feature-request", "discussion"],
        [
            (
                "charlie",
                "Yes please! I've been wanting this. A markdown toolbar would lower the barrier for non-technical users.",
                [
                    (
                        "hannah",
                        "Exactly. Not everyone knows that `**bold**` makes **bold** text.",
                    ),
                ],
            ),
            (
                "alice",
                "There are good React markdown editors out there. `react-markdown` + `remark-gfm` for rendering, and we could build a simple toolbar.",
                [
                    (
                        "evan",
                        "I could help with this! Would be a great first contribution.",
                    ),
                    (
                        "alice",
                        "That would be awesome Evan! Let's coordinate in the chat.",
                    ),
                ],
            ),
            ("admin", "Adding this to the roadmap. Great suggestion Hannah!", []),
        ],
    ),
    # Thread 17: Dark mode polish
    (
        "feedback",
        "evan",
        "Dark mode improvements",
        "The dark mode is great but I have a few suggestions:\n\n"
        "1. **Code blocks**: The background blends too much with the card background. Need more contrast.\n"
        "2. **Scrollbar styling**: The default browser scrollbar is very bright in dark mode.\n"
        "3. **Image borders**: Images with dark backgrounds disappear into the page.\n"
        "4. **Link colors**: The visited link color is hard to distinguish from unvisited.\n\n"
        "Screenshots attached (well, they would be if we had attachments!).",
        ["css", "feature-request", "bug"],  # Tagged as both feature-request AND bug
        [
            (
                "charlie",
                "Good catches! The code block contrast is an easy fix. I'll bump the background from `#1a1a1b` to `#272729`.",
                [
                    (
                        "evan",
                        "That would help a lot. The current contrast ratio is barely above 1.5:1.",
                    ),
                ],
            ),
            (
                "hannah",
                "For scrollbar styling, we can use `::-webkit-scrollbar` properties. Won't work in Firefox but covers most users.",
                [
                    (
                        "charlie",
                        "Firefox supports `scrollbar-color` and `scrollbar-width` now. We can cover both.",
                    ),
                ],
            ),
            (
                "alice",
                "The link color issue is real. I sometimes can't tell if I've already visited a thread.",
                [],
            ),
        ],
    ),
    # =========================================================================
    # OFF-TOPIC (2 threads)
    # =========================================================================
    # Thread 18: Development environment share
    (
        "offtopic",
        "charlie",
        "What's your development environment?",
        "Curious about everyone's setup!\n\n"
        "Mine:\n"
        "- **OS**: macOS Sonoma\n"
        "- **Editor**: VS Code with Vim keybindings\n"
        "- **Terminal**: iTerm2 + Oh My Zsh\n"
        "- **Browser**: Firefox Developer Edition\n"
        "- **Font**: JetBrains Mono\n\n"
        "What about you?",
        ["discussion"],
        [
            (
                "alice",
                "- **OS**: Ubuntu 24.04\n- **Editor**: VS Code (basic setup)\n- **Terminal**: Alacritty + Starship\n- **Font**: Fira Code with ligatures",
                [
                    (
                        "ivan",
                        "Alacritty is great! I switched from Kitty and never looked back.",
                    ),
                ],
            ),
            (
                "bob",
                "- **OS**: Arch Linux (btw)\n- **Editor**: Neovim with LazyVim\n- **Terminal**: foot\n- I live in the terminal.",
                [
                    (
                        "charlie",
                        "I knew there'd be an Arch user. Do you run PulseBoard in Docker or native?",
                    ),
                    (
                        "bob",
                        "Docker Compose, always. Consistent environments for everyone.",
                    ),
                ],
            ),
            (
                "george",
                "- **OS**: Windows 11 + WSL2\n- **Editor**: JetBrains suite (PyCharm + WebStorm)\n- **Terminal**: Windows Terminal\n\nWSL2 is surprisingly good for development.",
                [
                    ("luna", "Same setup! WSL2 + Docker Desktop is a solid combo."),
                ],
            ),
            (
                "diana",
                "- **OS**: macOS\n- **Editor**: JupyterLab for data work, VS Code for web dev\n- **Terminal**: iTerm2\n- I use two editors and I'm not ashamed.",
                [
                    ("hannah", "Nothing wrong with using the right tool for the job!"),
                ],
            ),
            (
                "julia",
                "- **OS**: macOS\n- **Editor**: Cursor (AI-powered VS Code fork)\n- **Terminal**: Warp\n- The AI tools have genuinely sped up my workflow.",
                [],
            ),
        ],
    ),
    # Thread 19: Podcast/YouTube recommendations
    (
        "offtopic",
        "luna",
        "Favorite tech podcasts and YouTube channels?",
        "Looking for good tech content to consume during commutes.\n\n"
        "My current rotation:\n"
        "- **Podcasts**: Changelog, Syntax.fm, Software Engineering Daily\n"
        "- **YouTube**: Fireship, ThePrimeagen, Computerphile\n\n"
        "What else should I add?",
        ["discussion"],
        [
            (
                "george",
                "**CoRecursive** is excellent for deep-dive stories about software. Every episode is like a mini-documentary.",
                [
                    ("luna", "Never heard of it! Added to my queue."),
                ],
            ),
            (
                "alice",
                "**Talk Python to Me** for Python-specific content. Michael Kennedy does great interviews.",
                [],
            ),
            (
                "ivan",
                "**Fasterthanlime** on YouTube. The deep dives into Rust, networking, and HTTP are incredible.",
                [
                    ("bob", "Seconded. The HTTP/2 video blew my mind."),
                ],
            ),
            (
                "evan",
                "**Traversy Media** on YouTube is great for beginners. Clear explanations, practical projects.",
                [],
            ),
        ],
    ),
    # =========================================================================
    # HELP AND SUPPORT (3 threads)
    # =========================================================================
    # Thread 20: Local setup guide (beginner Q&A)
    (
        "help",
        "evan",
        "How do I set up PulseBoard locally?",
        "I cloned the repo but I'm having trouble getting everything running. "
        "The README mentions Docker Compose but I want to run services individually for development.\n\n"
        "Questions:\n"
        "1. What's the correct order for installing dependencies?\n"
        "2. Do I need PostgreSQL and Redis running locally?\n"
        "3. How do I run just the frontend?",
        ["beginner", "deployment"],
        [
            (
                "bob",
                "Here's the quick setup:\n\n"
                "1. `python -m venv .venv && source .venv/bin/activate`\n"
                "2. `pip install -e services/shared` (shared library first!)\n"
                "3. `pip install -r services/core/requirements.txt`\n"
                "4. `pip install -r services/community/requirements.txt`\n"
                "5. Start PostgreSQL and Redis (Docker is easiest)\n"
                "6. Run each service with `uvicorn app.main:app --port <port>`\n"
                "7. `cd frontend && npm install && npm run dev`",
                [
                    (
                        "evan",
                        "This is exactly what I needed! Installing shared first was the key step I was missing.",
                    ),
                    (
                        "admin",
                        "We should add this to the README. Thanks Bob for the clear instructions.",
                    ),
                ],
            ),
            (
                "alice",
                "For just the frontend, `cd frontend && npm install && npm run dev`. It'll start on port 5173. But you'll need the backend running for API calls.",
                [],
            ),
            (
                "luna",
                "Or just `docker compose up --build` for the full stack. It handles everything.",
                [
                    (
                        "evan",
                        "I'll try Docker first, then individual services for debugging. Thanks everyone!",
                    ),
                ],
            ),
        ],
    ),
    # Thread 21: Module import error (common beginner gotcha)
    (
        "help",
        "diana",
        "Getting 'module not found' errors with shared library",
        "I keep getting `ModuleNotFoundError: No module named 'shared'` when trying to run the core service.\n\n"
        "I've installed the requirements but it still can't find the shared package.\n\n"
        "```\nTraceback (most recent call last):\n  File \"app/main.py\", line 3\n    from shared.core.config import settings\nModuleNotFoundError: No module named 'shared'\n```\n\n"
        "What am I missing?",
        ["python", "beginner", "bug"],
        [
            (
                "modsara",
                "You need to install the shared library as an editable package:\n\n"
                "```bash\npip install -e services/shared\n```\n\n"
                "The `-e` flag makes it importable from anywhere in the venv. This must be done BEFORE installing service requirements.",
                [
                    (
                        "diana",
                        "That fixed it! I was doing `pip install -r services/shared/requirements.txt` which doesn't install the package itself.",
                    ),
                    (
                        "modsara",
                        "Common gotcha! The `setup.py` (or `pyproject.toml`) in `services/shared/` defines the package. `-r` only installs dependencies listed in requirements.txt.",
                    ),
                ],
            ),
            (
                "kyle",
                "Also make sure you're in the right virtual environment. `which python` should point to `.venv/bin/python`.",
                [],
            ),
        ],
    ),
    # Thread 22: WebSocket troubleshooting
    (
        "help",
        "julia",
        "WebSocket connection keeps dropping",
        "I'm testing the real-time features but my WebSocket connection drops after about 60 seconds.\n\n"
        "Browser console shows:\n"
        "```\nWebSocket connection to 'ws://localhost:8000/ws' failed: Connection closed\n```\n\n"
        "Is there a keep-alive mechanism?",
        ["websocket", "bug"],
        [
            (
                "george",
                "This is likely a proxy timeout issue. If you're behind nginx, add:\n\n"
                "```nginx\nproxy_read_timeout 86400;\nproxy_send_timeout 86400;\n```\n\n"
                "For local dev, it shouldn't be an issue unless something else is proxying.",
                [
                    (
                        "julia",
                        "I'm running through WSL2 + Windows. That might be adding a proxy layer.",
                    ),
                    (
                        "george",
                        "Ah, WSL2's NAT can cause issues. Try connecting directly to the gateway container's IP instead of localhost.",
                    ),
                ],
            ),
            (
                "bob",
                "The gateway's WebSocket endpoint should handle reconnection. Check if the frontend hooks have retry logic.",
                [
                    (
                        "alice",
                        "They do. `useThreadLiveUpdates.js` and `useChatRoom.js` both have reconnection with exponential backoff.",
                    ),
                ],
            ),
            (
                "modmax",
                "Also check Redis. If Redis goes down, the pub/sub bridge stops and WebSocket messages stop flowing. The connection itself might stay open but feel 'dead'.",
                [],
            ),
        ],
    ),
]

# -- Chat rooms and messages ---------------------------------------------------
# 5 chat rooms: 3 group rooms + 2 direct message (DM) conversations.
#
# Format: (room_name, room_type, creator_username, [member_usernames], [messages])
#   - ``room_type`` is either "group" (visible chat room) or "direct" (1-on-1 DM)
#   - ``creator_username`` is the user who created the room
#   - ``[member_usernames]`` includes the creator (they're also a member)
#   - ``[messages]`` is a flat list of (sender_username, body) tuples
#
# Messages are spaced out over time using a base timestamp + incremental
# offsets to simulate natural conversation flow (see the seed function).
# -----------------------------------------------------------------------------
CHAT_DATA = [
    # (room_name, room_type, creator_username, member_usernames, messages)
    # messages: list of (sender_username, body)
    # ----- Group room 1: General Chat (8 members, 18 messages) -----
    # The main community hangout -- casual, mixed topics
    (
        "General Chat",
        "group",
        "admin",
        ["admin", "alice", "bob", "charlie", "diana", "evan", "modmax", "hannah"],
        [
            (
                "admin",
                "Welcome to the general chat! Feel free to discuss anything here.",
            ),
            (
                "alice",
                "Hey everyone! Anyone working on something interesting this weekend?",
            ),
            (
                "bob",
                "Setting up a Kubernetes cluster for fun. Because that's what normal people do on weekends.",
            ),
            ("charlie", "I'm redesigning my portfolio site. Going full CSS Grid."),
            (
                "diana",
                "Building a sentiment analysis pipeline with FastAPI. Mixing my data science and web dev skills.",
            ),
            (
                "evan",
                "Trying to understand WebSockets. The PulseBoard codebase is actually a great learning resource.",
            ),
            (
                "modmax",
                "Remember, this chat is logged. Keep it friendly and professional!",
            ),
            (
                "hannah",
                "Working on accessibility audit for PulseBoard. Found some color contrast issues to fix.",
            ),
            (
                "alice",
                "Oh nice Hannah! Let me know if you need help with the frontend changes.",
            ),
            (
                "bob",
                "Pro tip: use `docker stats` to monitor resource usage while developing. Saved me from an OOM crash yesterday.",
            ),
            (
                "charlie",
                "Anyone tried the new CSS `:has()` selector? It's a game-changer for styling parent elements.",
            ),
            ("evan", "I haven't but it sounds cool. Is browser support good enough?"),
            ("charlie", "~95% global support now. Safe to use in production."),
            ("diana", "Random question: tabs or spaces?"),
            ("bob", "Spaces. 4 of them. PEP 8."),
            ("alice", "Spaces for Python, tabs for Go. When in Rome..."),
            ("admin", "PulseBoard enforces 4 spaces via Black. No debate needed!"),
            (
                "hannah",
                "The real answer is: whatever your formatter does. Let the tool decide.",
            ),
        ],
    ),
    # ----- Group room 2: Backend Dev (6 members, 12 messages) -----
    # Focused technical discussion about backend architecture
    (
        "Backend Dev",
        "group",
        "alice",
        ["alice", "modsara", "kyle", "george", "fiona", "ivan"],
        [
            (
                "alice",
                "Created this room for backend discussions. Less formal than the forum.",
            ),
            (
                "modsara",
                "Good idea! I've been working on optimizing the query for listing threads with vote counts.",
            ),
            (
                "kyle",
                "Have you tried using a subquery for the vote count instead of joining? It's often faster.",
            ),
            (
                "modsara",
                "I'll try that. Currently it's an N+1 situation when loading thread cards.",
            ),
            (
                "george",
                "We could also add a `vote_count` column to threads and update it via triggers or application logic.",
            ),
            (
                "fiona",
                "Denormalization? Bold move. But for read-heavy pages like the homepage, it makes sense.",
            ),
            (
                "ivan",
                "SQLAlchemy's `hybrid_property` could help. Calculate in Python for single objects, use a subquery for bulk loads.",
            ),
            (
                "alice",
                "Great suggestions everyone. I'll prototype the subquery approach first since it's the least invasive.",
            ),
            (
                "kyle",
                "Let me know if you need help with the EXPLAIN ANALYZE. Happy to pair on it.",
            ),
            (
                "modsara",
                "Also, should we add database indexes on `(entity_type, entity_id)` for the votes table?",
            ),
            (
                "kyle",
                "Absolutely. That composite index will speed up both vote counting and duplicate checking.",
            ),
            (
                "george",
                "While we're at it, the reactions table could use the same index pattern.",
            ),
        ],
    ),
    # ----- Group room 3: Frontend Dev (5 members, 11 messages) -----
    # CSS, React, and design system discussions
    (
        "Frontend Dev",
        "group",
        "charlie",
        ["charlie", "hannah", "julia", "evan", "alice"],
        [
            (
                "charlie",
                "Frontend crew assemble! This is our space for CSS debates and React discussions.",
            ),
            (
                "hannah",
                "First order of business: the thread card hover state needs work. The transition is too abrupt.",
            ),
            (
                "charlie",
                "Agreed. I'll add a 150ms ease-in-out transition. Should feel smoother.",
            ),
            (
                "julia",
                "Are we planning to add any animations? Framer Motion could be nice for page transitions.",
            ),
            (
                "alice",
                "Let's keep it light on animations. Plain CSS transitions should cover most cases.",
            ),
            (
                "evan",
                "I'm still learning CSS. The `global.css` file is massive. How do you navigate it?",
            ),
            (
                "charlie",
                "Use your editor's outline/symbol search. The file is organized into sections with comment headers.",
            ),
            (
                "hannah",
                "We should consider splitting it into multiple CSS files at some point. 3000+ lines is a lot.",
            ),
            (
                "charlie",
                "True, but for now the single file approach keeps things simple. No import order issues.",
            ),
            (
                "julia",
                "What about CSS custom properties for spacing? I see some hardcoded px values.",
            ),
            (
                "charlie",
                "Good catch. We should add spacing tokens to the design system. `--space-xs`, `--space-sm`, etc.",
            ),
        ],
    ),
    # ----- DM 1: alice & bob (9 messages) -----
    # Private conversation about pairing on CI/CD
    (
        "DM: alice & bob",
        "direct",  # Direct messages have exactly 2 members
        "alice",  # alice initiated the conversation
        ["alice", "bob"],
        [
            (
                "alice",
                "Hey Bob, did you see the Docker multi-stage build thread you posted? Great responses.",
            ),
            (
                "bob",
                "Thanks! Yeah, the community is really engaged. George's non-root user tip was a good addition.",
            ),
            (
                "alice",
                "Want to pair on the CI/CD pipeline this week? I have some ideas for caching.",
            ),
            (
                "bob",
                "Sure! Wednesday afternoon works for me. I'll set up a draft PR with the workflow file.",
            ),
            (
                "alice",
                "Perfect. I'll review the current Dockerfiles and see where we can optimize.",
            ),
            (
                "bob",
                "Sounds good. Also, have you noticed the Redis connection sometimes hangs in tests?",
            ),
            (
                "alice",
                "Yeah, that's why `publish_event()` silently swallows errors. No Redis needed for tests.",
            ),
            ("bob", "Smart. Who designed that?"),
            (
                "alice",
                "The original architects. It's a good pattern -- fail silently in non-critical paths.",
            ),
        ],
    ),
    # ----- DM 2: evan & modmax (7 messages) -----
    # Mentorship-style conversation about moderation tools
    (
        "DM: evan & modmax",
        "direct",
        "evan",  # evan initiated -- junior asking a moderator for guidance
        ["evan", "modmax"],
        [
            (
                "evan",
                "Hey modmax, I have a question about the moderation tools. How do content reports work?",
            ),
            (
                "modmax",
                "When a user reports content, it creates a `ContentReport` entry. Mods see it in the admin dashboard under the Reports tab.",
            ),
            ("evan", "And then the mod can resolve or dismiss it?"),
            (
                "modmax",
                "Exactly. Resolving can trigger a moderation action -- warn, suspend, or ban the user.",
            ),
            ("evan", "Got it. The workflow makes sense. Thanks for explaining!"),
            (
                "modmax",
                "Anytime! If you want to learn the mod tools, I can grant you moderator access in a test environment.",
            ),
            (
                "evan",
                "That would be great! I'd love to understand the full admin dashboard.",
            ),
        ],
    ),
]


# =============================================================================
# SEED FUNCTION
# =============================================================================
# This is the main entry point.  It orchestrates the creation of all seed
# data in a single database transaction.  If any step fails, the entire
# transaction is rolled back (atomicity).
#
# The function follows a strict creation order dictated by foreign key
# dependencies:
#
#   1.  Users            (no FK deps -- created first)
#   2.  Categories       (no FK deps, but may already exist from app startup)
#   3.  Tags             (no FK deps)
#   4.  CategoryModerators (FK -> users, categories)
#   5.  Threads + Posts  (FK -> users, categories; posts FK -> threads)
#       ThreadTags       (FK -> threads, tags)
#       ThreadSubscriptions (FK -> threads, users)
#       Votes            (FK -> users; polymorphic entity_type/entity_id)
#       Reactions         (FK -> users; polymorphic entity_type/entity_id)
#   6.  FriendRequests   (FK -> users)
#   7.  ChatRooms + Messages (FK -> users; messages FK -> rooms)
#   8.  ContentReports   (FK -> users)
#   9.  ModerationActions (FK -> users)
#   10. CategoryRequests (FK -> users)
#   11. Notifications    (FK -> users)
#   12. AuditLogs        (FK -> users)
#   13. Pin/lock special threads
#
# ``db.flush()`` after each batch tells SQLAlchemy to send INSERT statements
# to the database and populate auto-generated ``id`` fields, WITHOUT
# committing.  This is important because later steps need those IDs (e.g.
# thread.id for ThreadTag, post.id for nested replies).
#
# The single ``db.commit()`` at the end makes all changes permanent.
# If an exception occurs at any point, ``db.rollback()`` in the except
# block ensures the database is left in its original state.
# =============================================================================


def seed() -> None:
    """Populate the database with comprehensive demo data.

    This function is **idempotent**: it checks whether the ``admin`` user
    already exists and returns early if so.  This makes it safe to call
    from Docker entrypoints, CI scripts, or interactive sessions without
    risk of creating duplicate rows.

    The function creates data in foreign-key dependency order:
    users -> categories -> tags -> threads/posts -> votes/reactions ->
    friend requests -> chat rooms/messages -> reports -> mod actions ->
    category requests -> notifications -> audit logs.

    All changes are wrapped in a single database transaction.  On any
    failure, the transaction is rolled back and no partial data is left
    behind.

    Raises:
        Exception: Re-raises any exception after rolling back the
            transaction, so the caller sees the original error.
    """
    # Ensure all tables exist (runs CREATE TABLE IF NOT EXISTS).
    # In production this is handled by the app startup, but for standalone
    # seed runs (especially with SQLite) we need to call it explicitly.
    init_db()

    # Open a database session.  SessionLocal is a SQLAlchemy sessionmaker
    # configured by the shared library based on DATABASE_URL_OVERRIDE.
    db = SessionLocal()

    try:
        # =============================================================
        # IDEMPOTENCY CHECK
        # =============================================================
        # Query for the admin user.  If it exists, the database has
        # already been seeded -- exit early to avoid duplicates.
        #
        # Why check for "admin" specifically?  Because admin is the very
        # first user created below, so its existence reliably indicates
        # that the seed function has run to completion at least once.
        # =============================================================
        existing_admin = db.query(User).filter_by(username="admin").first()
        if existing_admin:
            print("[seed] Data already seeded (admin user exists). Skipping.")
            return

        print("[seed] Seeding database with demo data ...")

        # =================================================================
        # 1. USERS
        # =================================================================
        # Create all 16 user accounts.  Key points:
        #   - All share the same password hash (computed once above).
        #   - ``is_verified=True`` so they can log in without email flow.
        #   - ``is_active=True`` so no accounts are suspended/banned.
        #   - ``last_seen`` is set to a recent random time so the "online
        #     status" indicator (green dot) appears for some users.
        #
        # The ``users`` dict maps username -> User ORM object for easy
        # lookup when creating threads, posts, friend requests, etc.
        # =================================================================
        users: dict[str, User] = {}
        for username, email, role, bio in USERS_DATA:
            u = User(
                username=username,
                email=email,
                password_hash=DEMO_HASH,
                role=role,
                bio=bio,
                is_verified=True,  # Skip email verification for demo
                is_active=True,  # No suspended accounts in seed data
                last_seen=_recent(hours_max=72),  # Random time in last 3 days
            )
            db.add(u)
            users[username] = u

        # flush() sends INSERTs to the DB and populates each user's .id
        # field (auto-increment primary key).  We need these IDs for the
        # foreign key references in every subsequent step.
        db.flush()  # assign IDs
        print(f"  - Created {len(users)} users")

        # =================================================================
        # 2. CATEGORIES
        # =================================================================
        # Categories may already exist because the app's ``init_db()`` or
        # ``_run_migrations()`` can create default categories on startup.
        #
        # To handle this gracefully, we check each slug before inserting.
        # If a category with that slug already exists, we REUSE it (add it
        # to our lookup dict) rather than creating a duplicate.  This is
        # the "category reuse logic" mentioned in AGENTS.md.
        #
        # This pattern is important in real-world apps where seed scripts
        # must cooperate with application-level initialisation code.
        # =================================================================
        categories: dict[str, Category] = {}
        created_count = 0
        for title, slug, description in CATEGORIES_DATA:
            # Check if this category was already created by app startup
            existing = db.query(Category).filter(Category.slug == slug).first()
            if existing:
                categories[slug] = existing  # Reuse existing row
            else:
                c = Category(title=title, slug=slug, description=description)
                db.add(c)
                categories[slug] = c  # Track newly created row
                created_count += 1

        db.flush()
        print(
            f"  - Categories: {created_count} created, {len(categories) - created_count} reused"
        )

        # =================================================================
        # 3. TAGS
        # =================================================================
        # 20 tags covering the platform's main technology areas.
        # Tags are simple name-only rows; the many-to-many relationship
        # with threads is handled by the ThreadTag join table (step 5).
        # =================================================================
        tags: dict[str, Tag] = {}
        for name in TAGS_DATA:
            t = Tag(name=name)
            db.add(t)
            tags[name] = t

        db.flush()
        print(f"  - Created {len(tags)} tags")

        # =================================================================
        # 4. CATEGORY MODERATORS
        # =================================================================
        # Assign the 2 moderators to categories.  Each moderator manages
        # 4 categories, ensuring full coverage of all 8 categories.
        #
        # CategoryModerator is a join table linking users to categories,
        # granting them moderation powers (resolve reports, lock threads,
        # pin threads, etc.) within those specific categories.
        # =================================================================
        cat_mod_pairs = [
            ("modmax", "general"),  # modmax moderates General Discussion
            ("modmax", "backend"),  # modmax moderates Backend Engineering
            ("modsara", "frontend"),  # modsara moderates Frontend Engineering
            ("modsara", "devops"),  # modsara moderates DevOps
            ("modmax", "showandtell"),  # modmax moderates Show and Tell
            ("modsara", "feedback"),  # modsara moderates Feedback
            ("modmax", "offtopic"),  # modmax moderates Off-Topic
            ("modsara", "help"),  # modsara moderates Help and Support
        ]
        for uname, cat_slug in cat_mod_pairs:
            db.add(
                CategoryModerator(
                    user_id=users[uname].id,
                    category_id=categories[cat_slug].id,
                )
            )
        db.flush()
        print(f"  - Assigned {len(cat_mod_pairs)} category moderators")

        # =================================================================
        # 5. THREADS, POSTS, THREAD_TAGS, THREAD_SUBSCRIPTIONS, VOTES,
        #    REACTIONS
        # =================================================================
        # This is the largest and most complex seeding step.  For each
        # thread in THREADS_DATA, we create:
        #
        #   a) The Thread row itself
        #   b) ThreadTag join rows (linking thread to its tags)
        #   c) A ThreadSubscription for the author (auto-subscribe)
        #   d) Random additional ThreadSubscriptions (2-6 other users)
        #   e) Random Votes on the thread (4-12 voters)
        #   f) Random Reactions on the thread (1-4 emoji reactions)
        #   g) Top-level Post rows (direct replies to the thread)
        #   h) Votes and Reactions on each post
        #   i) Nested reply Posts (parent_post_id = parent post's ID)
        #   j) Votes on each nested reply
        #
        # Timestamps are carefully ordered: thread_ts < post_ts < reply_ts
        # so the chronological ordering makes sense in the UI.
        # =================================================================
        thread_count = 0
        post_count = 0
        vote_count = 0
        reaction_count = 0
        subscription_count = 0

        # Emoji palette for reactions -- matches the frontend reaction picker
        emojis = ["👍", "❤️", "🔥", "😂", "🎉", "🤔", "👀", "💯"]

        # All usernames except the bot (bot doesn't vote/react in seed data)
        all_usernames = [u for u in users if u != "pulse"]

        for cat_slug, author_uname, title, body, tag_names, posts_data in THREADS_DATA:
            # Create the thread with a random timestamp from the past 25 days
            thread_ts = _past(days_ago_max=25)
            thread = Thread(
                category_id=categories[cat_slug].id,
                author_id=users[author_uname].id,
                title=title,
                body=body,
                created_at=thread_ts,
                updated_at=thread_ts,  # Same as created_at (no edits yet)
            )
            db.add(thread)
            db.flush()  # Get thread.id for foreign key references below
            thread_count += 1

            # --- Thread Tags ---
            # Link this thread to its tags via the ThreadTag join table.
            # Only add tags that exist in our tags dict (defensive check).
            for tname in tag_names:
                if tname in tags:
                    db.add(ThreadTag(thread_id=thread.id, tag_id=tags[tname].id))

            # --- Thread Subscription for author ---
            # The thread creator is automatically subscribed to their own
            # thread.  This mirrors the real app behaviour where creating
            # a thread auto-subscribes you to notifications about replies.
            db.add(
                ThreadSubscription(
                    thread_id=thread.id,
                    user_id=users[author_uname].id,
                )
            )
            subscription_count += 1

            # --- Random additional subscriptions ---
            # 2-6 random users also subscribe (simulating users who follow
            # interesting threads).  We exclude the author to avoid a
            # duplicate subscription.
            for uname in random.sample(all_usernames, k=random.randint(2, 6)):
                if uname != author_uname:
                    db.add(
                        ThreadSubscription(
                            thread_id=thread.id,
                            user_id=users[uname].id,
                        )
                    )
                    subscription_count += 1

            # --- Votes on thread ---
            # 4-12 random users vote on each thread.
            # ``random.choices`` with ``weights=[85, 15]`` produces 85%
            # upvotes and 15% downvotes -- this mirrors real forum
            # engagement where most votes are positive.
            for uname in random.sample(all_usernames, k=random.randint(4, 12)):
                value = random.choices([1, -1], weights=[85, 15])[0]
                db.add(
                    Vote(
                        user_id=users[uname].id,
                        entity_type="thread",  # Polymorphic: votes can be on threads or posts
                        entity_id=thread.id,
                        value=value,  # +1 = upvote, -1 = downvote
                    )
                )
                vote_count += 1

            # --- Reactions on thread ---
            # 1-4 users add emoji reactions to each thread.
            # Reactions are separate from votes (you can upvote AND react).
            for uname in random.sample(all_usernames, k=random.randint(1, 4)):
                db.add(
                    Reaction(
                        user_id=users[uname].id,
                        entity_type="thread",
                        entity_id=thread.id,
                        emoji=random.choice(emojis),  # Random emoji from palette
                    )
                )
                reaction_count += 1

            # --- Posts (top-level replies to the thread) ---
            # Each post in posts_data is a tuple:
            #   (author_username, body_text, [nested_replies])
            for post_author_uname, post_body, replies_data in posts_data:
                # Post timestamp is 1-48 hours AFTER the thread was created,
                # simulating a natural gap between thread creation and first
                # replies appearing.
                post_ts = thread_ts + timedelta(
                    hours=random.randint(1, 48),
                    minutes=random.randint(0, 59),
                )
                post = Post(
                    thread_id=thread.id,
                    author_id=users[post_author_uname].id,
                    body=post_body,
                    parent_post_id=None,  # None = top-level reply (not nested)
                    created_at=post_ts,
                    updated_at=post_ts,
                )
                db.add(post)
                db.flush()  # Get post.id for nested replies and votes
                post_count += 1

                # --- Votes on top-level post ---
                # Slightly different weight than threads: 80/20 split for
                # posts (posts are more likely to get downvotes than threads).
                for uname in random.sample(all_usernames, k=random.randint(2, 8)):
                    value = random.choices([1, -1], weights=[80, 20])[0]
                    db.add(
                        Vote(
                            user_id=users[uname].id,
                            entity_type="post",
                            entity_id=post.id,
                            value=value,
                        )
                    )
                    vote_count += 1

                # --- Reactions on post (50% chance) ---
                # Not every post gets reactions -- the 50% coin flip makes
                # the data feel more natural.
                if random.random() > 0.5:
                    for uname in random.sample(all_usernames, k=random.randint(1, 3)):
                        db.add(
                            Reaction(
                                user_id=users[uname].id,
                                entity_type="post",
                                entity_id=post.id,
                                emoji=random.choice(emojis),
                            )
                        )
                        reaction_count += 1

                # --- Nested replies (children of this post) ---
                # These create the Reddit-style comment tree.  Each reply
                # has ``parent_post_id`` set to the parent post's ID,
                # which the frontend renders as indented sub-comments
                # with vertical collapse lines.
                for reply_author_uname, reply_body in replies_data:
                    # Reply timestamp is 1-24 hours after the parent post
                    reply_ts = post_ts + timedelta(
                        hours=random.randint(1, 24),
                        minutes=random.randint(0, 59),
                    )
                    reply = Post(
                        thread_id=thread.id,
                        author_id=users[reply_author_uname].id,
                        body=reply_body,
                        parent_post_id=post.id,  # THIS makes it a nested reply
                        created_at=reply_ts,
                        updated_at=reply_ts,
                    )
                    db.add(reply)
                    db.flush()  # Get reply.id for votes
                    post_count += 1

                    # --- Votes on nested reply ---
                    # Fewer voters (1-5) since nested replies get less
                    # visibility than top-level posts.
                    for uname in random.sample(all_usernames, k=random.randint(1, 5)):
                        value = random.choices([1, -1], weights=[80, 20])[0]
                        db.add(
                            Vote(
                                user_id=users[uname].id,
                                entity_type="post",
                                entity_id=reply.id,
                                value=value,
                            )
                        )
                        vote_count += 1

        db.flush()
        print(f"  - Created {thread_count} threads")
        print(f"  - Created {post_count} posts (including nested replies)")
        print(f"  - Created {vote_count} votes")
        print(f"  - Created {reaction_count} reactions")
        print(f"  - Created {subscription_count} thread subscriptions")

        # =================================================================
        # 6. FRIEND REQUESTS
        # =================================================================
        # 18 friend request records demonstrating all 3 lifecycle states:
        #   - ACCEPTED (13): the two users are now friends
        #   - PENDING  (4):  request sent but not yet responded to
        #   - DECLINED (1):  request was rejected
        #
        # This data lets the frontend show:
        #   - Friend lists on profile pages (accepted requests)
        #   - Pending request badges on the friends tab
        #   - The "Add Friend" vs "Request Pending" button states
        #
        # For ACCEPTED and DECLINED requests, ``responded_at`` is set to
        # a random past date (simulating when the response happened).
        # PENDING requests have no ``responded_at`` (still waiting).
        # =================================================================
        friend_pairs = [
            # --- 13 Accepted friendships ---
            ("alice", "bob", FriendRequestStatus.ACCEPTED),  # Full-stack + DevOps
            ("alice", "charlie", FriendRequestStatus.ACCEPTED),  # Full-stack + Frontend
            ("alice", "hannah", FriendRequestStatus.ACCEPTED),  # Full-stack + UX
            ("bob", "george", FriendRequestStatus.ACCEPTED),  # DevOps + Tech lead
            ("bob", "luna", FriendRequestStatus.ACCEPTED),  # DevOps + Cloud
            (
                "charlie",
                "hannah",
                FriendRequestStatus.ACCEPTED,
            ),  # Frontend + UX (natural pair)
            ("charlie", "julia", FriendRequestStatus.ACCEPTED),  # Frontend + Mobile
            (
                "diana",
                "evan",
                FriendRequestStatus.ACCEPTED,
            ),  # Data science + Junior dev
            ("diana", "modsara", FriendRequestStatus.ACCEPTED),  # Member + Moderator
            ("fiona", "ivan", FriendRequestStatus.ACCEPTED),  # Security + OSS
            ("george", "kyle", FriendRequestStatus.ACCEPTED),  # Tech lead + DBA
            ("modmax", "modsara", FriendRequestStatus.ACCEPTED),  # Mod team friendship
            (
                "modmax",
                "admin",
                FriendRequestStatus.ACCEPTED,
            ),  # Mod + Admin (staff bond)
            # --- 4 Pending requests (awaiting response) ---
            ("evan", "alice", FriendRequestStatus.PENDING),  # Junior reaching out
            ("julia", "george", FriendRequestStatus.PENDING),  # Mobile dev -> Tech lead
            ("kyle", "fiona", FriendRequestStatus.PENDING),  # DBA -> Security
            ("luna", "diana", FriendRequestStatus.PENDING),  # Cloud -> Data science
            # --- 1 Declined request ---
            (
                "ivan",
                "charlie",
                FriendRequestStatus.DECLINED,
            ),  # OSS -> Frontend (declined)
        ]
        for requester, recipient, status in friend_pairs:
            fr = FriendRequest(
                requester_id=users[requester].id,
                recipient_id=users[recipient].id,
                status=status,
            )
            # Set responded_at timestamp for requests that have been acted on.
            # Pending requests don't have a response timestamp yet.
            if status in (FriendRequestStatus.ACCEPTED, FriendRequestStatus.DECLINED):
                fr.responded_at = _past(days_ago_max=15)
            db.add(fr)

        db.flush()
        print(f"  - Created {len(friend_pairs)} friend requests")

        # =================================================================
        # 7. CHAT ROOMS AND MESSAGES
        # =================================================================
        # 5 rooms total: 3 group chat rooms + 2 direct messages.
        #
        # For each room:
        #   a) Create the ChatRoom row
        #   b) Create ChatRoomMember rows for each participant
        #   c) Create Message rows with incrementally spaced timestamps
        #
        # Message timestamps start from a random base time (up to 10 days
        # ago) and increment by 5-45 minutes per message.  This simulates
        # natural conversation flow where messages arrive at irregular but
        # plausible intervals.
        # =================================================================
        room_count = 0
        msg_count = 0

        for (
            room_name,
            room_type,
            creator_uname,
            member_unames,
            messages_data,
        ) in CHAT_DATA:
            room = ChatRoom(
                name=room_name,
                room_type=room_type,  # "group" or "direct"
                created_by_id=users[creator_uname].id,
            )
            db.add(room)
            db.flush()  # Get room.id for members and messages
            room_count += 1

            # --- Room members ---
            # Every username in the member list gets a ChatRoomMember row.
            # The creator is included in the member list (they're a member too).
            for muname in member_unames:
                db.add(
                    ChatRoomMember(
                        room_id=room.id,
                        user_id=users[muname].id,
                    )
                )

            db.flush()

            # --- Messages ---
            # Messages are created in chronological order.  ``base_ts`` is
            # when the conversation started; each subsequent message is
            # offset by ``i * random(5..45)`` minutes from the base.
            base_ts = _past(days_ago_max=10)
            for i, (sender_uname, body) in enumerate(messages_data):
                msg_ts = base_ts + timedelta(minutes=i * random.randint(5, 45))
                msg = Message(
                    room_id=room.id,
                    sender_id=users[sender_uname].id,
                    body=body,
                    created_at=msg_ts,
                    updated_at=msg_ts,
                )
                db.add(msg)
                msg_count += 1

        db.flush()
        print(f"  - Created {room_count} chat rooms")
        print(f"  - Created {msg_count} messages")

        # =================================================================
        # 8. CONTENT REPORTS
        # =================================================================
        # 5 sample reports demonstrating the content moderation pipeline:
        #   - 2 PENDING   (awaiting moderator review)
        #   - 2 RESOLVED  (moderator took action)
        #   - 1 DISMISSED (reported content was fine, report rejected)
        #
        # Format: (reporter_username, entity_type, entity_id, reason,
        #          status, resolved_by_username_or_None)
        #
        # ``entity_id`` values are approximate -- they reference rows
        # created in step 5 above.  In a real app, these would be exact
        # foreign keys, but for seed data the specific IDs matter less
        # than having a realistic variety of report types and statuses.
        # =================================================================
        # A few sample reports for the admin dashboard
        reports_data = [
            # Pending reports -- moderators will see these in the admin dashboard
            (
                "fiona",  # Reporter: the security researcher
                "post",  # Reported entity type
                3,  # Reported entity ID (post #3)
                "This post contains misleading information about security practices.",
                "pending",  # Status: awaiting mod review
                None,  # No resolver yet
            ),
            (
                "hannah",  # Reporter: the UX designer
                "thread",  # Reporting an entire thread
                18,  # Thread #18 (dev environment thread)
                "Title is clickbait / misleading.",
                "pending",
                None,
            ),
            # Resolved reports -- moderator reviewed and took action
            (
                "modmax",  # Even moderators can report content
                "post",
                10,
                "Off-topic reply that derails the discussion.",
                "resolved",  # Status: moderator resolved this
                "modmax",  # Resolved by same person (self-handled)
            ),
            (
                "alice",
                "post",
                15,
                "Potential spam / self-promotion.",
                "resolved",
                "modsara",  # Resolved by a different moderator
            ),
            # Dismissed report -- reported content was deemed acceptable
            (
                "george",
                "thread",
                5,
                "Duplicate thread, same topic was already discussed.",
                "dismissed",  # Status: report rejected by moderator
                "modmax",
            ),
        ]
        for (
            reporter_uname,
            entity_type,
            entity_id,
            reason,
            status,
            resolved_by_uname,
        ) in reports_data:
            report = ContentReport(
                reporter_id=users[reporter_uname].id,
                entity_type=entity_type,
                entity_id=entity_id,
                reason=reason,
                status=status,
            )
            # Set resolution metadata for non-pending reports
            if resolved_by_uname:
                report.resolved_by = users[resolved_by_uname].id
                report.resolved_at = _past(days_ago_max=5)
            db.add(report)

        db.flush()
        print(f"  - Created {len(reports_data)} content reports")

        # =================================================================
        # 9. MODERATION ACTIONS
        # =================================================================
        # 2 warning actions demonstrating the moderation system.
        # Action types in the real app: "warn", "suspend", "ban".
        #
        # We only seed warnings (the mildest action) because suspend/ban
        # would require setting ``is_active=False`` on the target user,
        # which would prevent them from logging in during demos.
        #
        # Format: (moderator_username, target_username, action_type,
        #          reason, duration_hours_or_None, report_id_or_None)
        # =================================================================
        mod_actions_data = [
            (
                "modmax",  # Moderator who issued the action
                "ivan",  # User receiving the warning
                "warn",  # Action type (warn | suspend | ban)
                "Off-topic posting in the Backend Engineering category.",
                None,  # duration_hours: None for warnings
                None,  # report_id: not linked to a specific report
            ),
            (
                "modsara",
                "kyle",
                "warn",
                "Please keep discussions respectful.",
                None,
                None,
            ),
        ]
        for (
            mod_uname,
            target_uname,
            action_type,
            reason,
            duration,
            report_id,
        ) in mod_actions_data:
            db.add(
                ModerationAction(
                    moderator_id=users[mod_uname].id,
                    target_user_id=users[target_uname].id,
                    action_type=action_type,
                    reason=reason,
                    duration_hours=duration,  # None for warns; hours for suspensions
                    report_id=report_id,  # Optional link to originating report
                )
            )

        db.flush()
        print(f"  - Created {len(mod_actions_data)} moderation actions")

        # =================================================================
        # 10. CATEGORY REQUESTS
        # =================================================================
        # 4 requests from users asking admin to create new categories.
        # Demonstrates the category request workflow with all 3 statuses:
        #   - 2 PENDING  (awaiting admin review)
        #   - 1 APPROVED (admin accepted the request)
        #   - 1 REJECTED (admin denied the request)
        #
        # Format: (requester_username, title, slug, description, status,
        #          reviewer_username_or_None)
        #
        # Note: approved requests don't automatically create the category
        # in this seed data -- the admin would do that manually.
        # =================================================================
        cat_requests_data = [
            (
                "diana",  # Data scientist requesting an ML category
                "Machine Learning",
                "ml",
                "Discuss ML models, training, and deployment.",
                "pending",  # Still awaiting admin review
                None,
            ),
            (
                "ivan",  # OSS contributor requesting an open-source category
                "Open Source",
                "opensource",
                "Collaboration on open-source projects.",
                "approved",  # Admin approved this request
                "admin",  # Reviewed by admin
            ),
            (
                "julia",  # Mobile dev requesting a mobile category
                "Mobile Development",
                "mobile",
                "React Native, Flutter, and native mobile dev.",
                "pending",
                None,
            ),
            (
                "george",  # Tech lead requesting system design category
                "System Design",
                "systemdesign",
                "Architecture patterns and system design discussions.",
                "rejected",  # Admin rejected -- perhaps too niche
                "admin",
            ),
        ]
        for (
            requester_uname,
            title,
            slug,
            desc,
            status,
            reviewer_uname,
        ) in cat_requests_data:
            cr = CategoryRequest(
                requester_id=users[requester_uname].id,
                title=title,
                slug=slug,
                description=desc,
                status=status,
            )
            # Set review metadata for non-pending requests
            if reviewer_uname:
                cr.reviewed_by = users[reviewer_uname].id
                cr.reviewed_at = _past(days_ago_max=5)
            db.add(cr)

        db.flush()
        print(f"  - Created {len(cat_requests_data)} category requests")

        # =================================================================
        # 11. NOTIFICATIONS
        # =================================================================
        # 15 notifications of various types, demonstrating all notification
        # categories the platform supports:
        #
        #   - "reply"          (7): Someone replied to your thread/post
        #   - "mention"        (1): Someone @mentioned you
        #   - "friend_request" (1): New incoming friend request
        #   - "friend_accept"  (1): Your friend request was accepted
        #   - "report"         (2): New content report (sent to staff)
        #   - "mod_warning"    (1): You received a moderator warning
        #
        # Each notification has a ``payload`` dict containing contextual
        # data (thread_id, post_id, from_user, etc.) that the frontend
        # uses to render the notification and link to the right page.
        #
        # ``is_read`` is randomly set with a 60% read / 40% unread split,
        # so the notification bell shows a realistic badge count.
        #
        # The ``thread_id`` and ``post_id`` values in payloads are
        # approximate references to the seed data created above.
        # =================================================================
        notif_data = [
            # Reply notifications -- the most common type
            (
                "alice",  # Recipient of the notification
                "reply",  # Notification type
                "New reply to your thread",  # Display title
                {"thread_id": 1, "post_id": 1, "from_user": "bob"},  # Context payload
            ),
            (
                "alice",
                "reply",
                "New reply to your thread",
                {"thread_id": 4, "post_id": 8, "from_user": "george"},
            ),
            # Mention notification
            (
                "bob",
                "mention",
                "You were mentioned in a post",
                {"thread_id": 11, "post_id": 25, "from_user": "alice"},
            ),
            # Friend request notification
            (
                "charlie",
                "friend_request",
                "New friend request",
                {"from_user": "ivan", "request_id": 18},
            ),
            # More reply notifications (distributed across different users)
            (
                "evan",
                "reply",
                "modmax replied to your post",
                {"thread_id": 20, "post_id": 42, "from_user": "modmax"},
            ),
            # Report notifications -- sent to admin/moderators
            (
                "admin",
                "report",
                "New content report submitted",
                {"report_id": 1, "reporter": "fiona"},
            ),
            (
                "modmax",
                "report",
                "New content report submitted",
                {"report_id": 2, "reporter": "hannah"},
            ),
            # More reply notifications
            (
                "hannah",
                "reply",
                "charlie replied to your thread",
                {"thread_id": 9, "post_id": 20, "from_user": "charlie"},
            ),
            # Friend accept notification
            (
                "diana",
                "friend_accept",
                "modsara accepted your friend request",
                {"from_user": "modsara"},
            ),
            # Additional reply notifications across various users
            (
                "george",
                "reply",
                "fiona replied to your thread",
                {"thread_id": 2, "post_id": 4, "from_user": "fiona"},
            ),
            (
                "kyle",
                "reply",
                "ivan replied to your thread",
                {"thread_id": 5, "post_id": 12, "from_user": "ivan"},
            ),
            (
                "julia",
                "reply",
                "alice replied to your thread",
                {"thread_id": 10, "post_id": 22, "from_user": "alice"},
            ),
            (
                "fiona",
                "reply",
                "bob replied to your thread",
                {"thread_id": 7, "post_id": 16, "from_user": "bob"},
            ),
            (
                "luna",
                "reply",
                "bob replied to your thread",
                {"thread_id": 12, "post_id": 28, "from_user": "bob"},
            ),
            # Moderation warning notification
            (
                "ivan",
                "mod_warning",
                "You received a warning from modmax",
                {"action_id": 1, "moderator": "modmax"},
            ),
        ]
        for user_uname, notif_type, title, payload in notif_data:
            # 60% of notifications are marked as read (random.random() > 0.4),
            # giving the UI a realistic mix of read/unread items.
            is_read = random.random() > 0.4  # 60% read
            db.add(
                Notification(
                    user_id=users[user_uname].id,
                    notification_type=notif_type,
                    title=title,
                    payload=payload,  # JSON-serialisable dict stored as JSON column
                    is_read=is_read,
                )
            )

        db.flush()
        print(f"  - Created {len(notif_data)} notifications")

        # =================================================================
        # 12. AUDIT LOGS
        # =================================================================
        # 30 audit log entries providing a chronological record of
        # important platform actions.  The audit log is used by the admin
        # dashboard's "Activity Log" tab for compliance and debugging.
        #
        # Format: (actor_username, action_constant, entity_type,
        #          entity_id, human_readable_details)
        #
        # Action constants (e.g. "user_register", "thread_create") match
        # the constants defined in ``shared/services/audit.py``.
        #
        # Each entry gets a random private IP address (192.168.1.x) to
        # simulate the ``ip_address`` field that the real app captures
        # from the HTTP request.
        #
        # The entries tell a story: admin created the platform, promoted
        # moderators, created categories, users registered, content was
        # posted, reports were filed and resolved, etc.
        # =================================================================
        audit_data = [
            # --- User registration events ---
            ("admin", "user_register", "user", 1, "Admin account created"),
            ("modmax", "user_register", "user", 2, "Moderator modmax registered"),
            ("modsara", "user_register", "user", 3, "Moderator modsara registered"),
            ("alice", "user_register", "user", 4, "User alice registered"),
            ("bob", "user_register", "user", 5, "User bob registered"),
            # --- Role promotions (admin promoting users to moderator) ---
            ("admin", "user_role_change", "user", 2, "Changed role to moderator"),
            ("admin", "user_role_change", "user", 3, "Changed role to moderator"),
            # --- Category creation events (admin bootstrapping the forum) ---
            (
                "admin",
                "category_create",
                "category",
                1,
                "Created category: General Discussion",
            ),
            (
                "admin",
                "category_create",
                "category",
                2,
                "Created category: Backend Engineering",
            ),
            (
                "admin",
                "category_create",
                "category",
                3,
                "Created category: Frontend Engineering",
            ),
            (
                "admin",
                "category_create",
                "category",
                4,
                "Created category: DevOps and Deployment",
            ),
            (
                "admin",
                "category_create",
                "category",
                5,
                "Created category: Show and Tell",
            ),
            (
                "admin",
                "category_create",
                "category",
                6,
                "Created category: Feedback and Suggestions",
            ),
            ("admin", "category_create", "category", 7, "Created category: Off-Topic"),
            (
                "admin",
                "category_create",
                "category",
                8,
                "Created category: Help and Support",
            ),
            # --- Thread creation events ---
            ("admin", "thread_create", "thread", 1, "Created welcome thread"),
            (
                "alice",
                "thread_create",
                "thread",
                4,
                "Created thread: FastAPI dependency injection",
            ),
            (
                "kyle",
                "thread_create",
                "thread",
                5,
                "Created thread: PostgreSQL optimization",
            ),
            # --- Category moderator assignments ---
            (
                "modmax",
                "category_mod_assign",
                "category",
                1,
                "Assigned modmax to General Discussion",
            ),
            (
                "modsara",
                "category_mod_assign",
                "category",
                3,
                "Assigned modsara to Frontend Engineering",
            ),
            # --- Content report events ---
            (
                "fiona",
                "report_create",
                "post",
                3,
                "Reported post for misleading security info",
            ),
            (
                "modmax",
                "report_resolve",
                "post",
                10,
                "Resolved report: off-topic reply",
            ),
            # --- Moderation action events ---
            ("modmax", "mod_action", "user", 12, "Warned ivan for off-topic posting"),
            # --- Profile update events ---
            ("alice", "user_profile_update", "user", 4, "Updated bio"),
            ("bob", "user_profile_update", "user", 5, "Updated bio"),
            # --- Chat room creation events ---
            ("admin", "chat_room_create", "chat_room", 1, "Created General Chat room"),
            ("alice", "chat_room_create", "chat_room", 2, "Created Backend Dev room"),
            (
                "charlie",
                "chat_room_create",
                "chat_room",
                3,
                "Created Frontend Dev room",
            ),
            # --- Friend request events ---
            ("alice", "friend_request_send", "user", 5, "Sent friend request to bob"),
            (
                "bob",
                "friend_request_accept",
                "user",
                4,
                "Accepted friend request from alice",
            ),
        ]
        for actor_uname, action, entity_type, entity_id, details in audit_data:
            db.add(
                AuditLog(
                    actor_id=users[actor_uname].id,
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    details=details,
                    # Simulated private IP address for the audit record.
                    # Real app captures this from the HTTP request headers.
                    ip_address=f"192.168.1.{random.randint(10, 250)}",
                )
            )

        db.flush()
        print(f"  - Created {len(audit_data)} audit log entries")

        # =================================================================
        # 13. PIN the welcome thread, LOCK the guidelines thread
        # =================================================================
        # Special thread states that demonstrate the moderation features:
        #
        # - PINNED threads appear at the top of their category's thread
        #   list, regardless of sort order.  Used for important announcements.
        #
        # - LOCKED threads cannot receive new replies.  Used for reference
        #   material that shouldn't be buried under discussion.
        #
        # We query by title rather than hardcoding IDs because thread IDs
        # depend on insertion order and database state.
        # =================================================================
        # Thread 1 = Welcome, Thread 3 = Community Guidelines
        welcome_thread = (
            db.query(Thread).filter_by(title="Welcome to PulseBoard!").first()
        )
        if welcome_thread:
            welcome_thread.is_pinned = True  # Pinned to top of General Discussion

        guidelines_thread = (
            db.query(Thread)
            .filter_by(title="Community Guidelines - Please Read")
            .first()
        )
        if guidelines_thread:
            guidelines_thread.is_pinned = True  # Also pinned to top
            guidelines_thread.is_locked = True  # No new replies allowed

        # =================================================================
        # COMMIT -- make all changes permanent
        # =================================================================
        # Everything above used flush() (which sends SQL to the DB but
        # doesn't commit).  This single commit() makes the entire seed
        # operation atomic:
        #   - If everything succeeded -> all data is saved
        #   - If anything failed -> the except block rolls back everything
        # =================================================================
        db.commit()

        # =================================================================
        # FINAL SUMMARY
        # =================================================================
        # Print a human-readable summary of everything that was created.
        # Useful for verifying the seed ran correctly and for demos.
        # =================================================================
        print("\n[seed] Done! Database seeded successfully.")
        print(
            f"\n  Total: {len(users)} users, {len(categories)} categories, "
            f"{len(tags)} tags, {thread_count} threads, {post_count} posts"
        )
        print(
            f"  {vote_count} votes, {reaction_count} reactions, "
            f"{len(friend_pairs)} friend requests"
        )
        print(
            f"  {room_count} chat rooms, {msg_count} messages, "
            f"{len(notif_data)} notifications"
        )
        print(
            f"  {len(reports_data)} reports, {len(mod_actions_data)} mod actions, "
            f"{len(audit_data)} audit logs"
        )
        print("\n  All accounts use password: password123")
        print("  Admin login: admin / password123")

    except Exception:
        # Roll back the entire transaction on ANY failure.
        # This ensures no partial/corrupt data is left in the database.
        # The exception is re-raised so the caller sees the original error.
        db.rollback()
        raise
    finally:
        # Always close the session to release the database connection
        # back to the pool, even if an exception occurred.
        db.close()


# ---------------------------------------------------------------------------
# Script entry point
# ---------------------------------------------------------------------------
# ``if __name__ == "__main__":`` ensures ``seed()`` only runs when the file
# is executed directly (``python services/seed.py``), not when it's imported
# as a module by tests or other scripts.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    seed()
