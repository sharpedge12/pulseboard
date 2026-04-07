"""
Comprehensive seed script for PulseBoard.

Populates the database with realistic dummy data for demo / showcase purposes.

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
"""

from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Make sure the shared package is importable regardless of where we run from.
# ---------------------------------------------------------------------------
_project_root = Path(__file__).resolve().parent.parent
_services_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_services_dir))
sys.path.insert(0, str(_services_dir / "shared"))

# ---------------------------------------------------------------------------
# Allow running outside Docker by defaulting to SQLite when PostgreSQL is
# not available.  Pass --sqlite flag or set DATABASE_URL_OVERRIDE env var.
# ---------------------------------------------------------------------------
if "--sqlite" in sys.argv or not os.environ.get("DATABASE_URL_OVERRIDE"):
    _sqlite_path = _project_root / "seed_data.db"
    os.environ.setdefault(
        "DATABASE_URL_OVERRIDE",
        f"sqlite:///{_sqlite_path}",
    )

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
random.seed(42)

# ---------------------------------------------------------------------------
# Helper: timestamps spread over the last 30 days
# ---------------------------------------------------------------------------
_NOW = datetime.now(timezone.utc)


def _past(days_ago_max: int = 30) -> datetime:
    """Return a random timestamp in the last *days_ago_max* days."""
    delta = timedelta(
        days=random.randint(0, days_ago_max),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )
    return _NOW - delta


def _recent(hours_max: int = 48) -> datetime:
    """Return a random timestamp in the last *hours_max* hours."""
    delta = timedelta(
        hours=random.randint(0, hours_max),
        minutes=random.randint(0, 59),
    )
    return _NOW - delta


# ---------------------------------------------------------------------------
# Password (shared for all demo users)
# ---------------------------------------------------------------------------
DEMO_PASSWORD = "password123"
DEMO_HASH = hash_password(DEMO_PASSWORD)

# ===== DATA =================================================================

# -- Categories ---------------------------------------------------------------
CATEGORIES_DATA = [
    (
        "General Discussion",
        "general",
        "Project updates, questions, and broad discussion.",
    ),
    (
        "Backend Engineering",
        "backend",
        "API design, FastAPI, databases, and infrastructure.",
    ),
    ("Frontend Engineering", "frontend", "React UI, UX, and integration work."),
    (
        "DevOps and Deployment",
        "devops",
        "Docker, Redis, Render, Vercel, and deployment notes.",
    ),
    (
        "Show and Tell",
        "showandtell",
        "Share your projects, demos, or cool things you built.",
    ),
    (
        "Feedback and Suggestions",
        "feedback",
        "Ideas and suggestions for improving PulseBoard.",
    ),
    ("Off-Topic", "offtopic", "Anything that doesn't fit elsewhere."),
    ("Help and Support", "help", "Ask for help with bugs, setup, or usage questions."),
]

# -- Tags ---------------------------------------------------------------------
TAGS_DATA = [
    "python",
    "fastapi",
    "react",
    "docker",
    "postgresql",
    "redis",
    "javascript",
    "css",
    "websocket",
    "jwt",
    "oauth",
    "testing",
    "performance",
    "security",
    "deployment",
    "beginner",
    "discussion",
    "bug",
    "feature-request",
    "tutorial",
]

# -- Users --------------------------------------------------------------------
USERS_DATA = [
    # (username, email, role, bio)
    (
        "admin",
        "admin@pulseboard.app",
        UserRole.ADMIN,
        "PulseBoard administrator. I keep the lights on.",
    ),
    (
        "modmax",
        "modmax@pulseboard.app",
        UserRole.MODERATOR,
        "Community moderator. Happy to help maintain quality discussions.",
    ),
    (
        "modsara",
        "modsara@pulseboard.app",
        UserRole.MODERATOR,
        "Moderator and backend enthusiast. FastAPI fan.",
    ),
    (
        "alice",
        "alice@pulseboard.app",
        UserRole.MEMBER,
        "Full-stack developer. Love React and Python equally.",
    ),
    (
        "bob",
        "bob@pulseboard.app",
        UserRole.MEMBER,
        "DevOps engineer. Docker, Kubernetes, and CI/CD pipelines.",
    ),
    (
        "charlie",
        "charlie@pulseboard.app",
        UserRole.MEMBER,
        "Frontend wizard. CSS is my superpower.",
    ),
    (
        "diana",
        "diana@pulseboard.app",
        UserRole.MEMBER,
        "Data scientist exploring web dev. New to FastAPI.",
    ),
    (
        "evan",
        "evan@pulseboard.app",
        UserRole.MEMBER,
        "Junior developer learning the ropes. Eager to contribute!",
    ),
    (
        "fiona",
        "fiona@pulseboard.app",
        UserRole.MEMBER,
        "Security researcher. Always thinking about edge cases.",
    ),
    (
        "george",
        "george@pulseboard.app",
        UserRole.MEMBER,
        "Tech lead at a startup. Interested in microservices.",
    ),
    (
        "hannah",
        "hannah@pulseboard.app",
        UserRole.MEMBER,
        "UX designer who codes. Bridging design and engineering.",
    ),
    (
        "ivan",
        "ivan@pulseboard.app",
        UserRole.MEMBER,
        "Open-source contributor. Rust and Python are my go-to languages.",
    ),
    (
        "julia",
        "julia@pulseboard.app",
        UserRole.MEMBER,
        "Mobile developer branching into web. React Native -> React.",
    ),
    (
        "kyle",
        "kyle@pulseboard.app",
        UserRole.MEMBER,
        "Database nerd. PostgreSQL, SQLite, and query optimization.",
    ),
    (
        "luna",
        "luna@pulseboard.app",
        UserRole.MEMBER,
        "Cloud architect. AWS, GCP, and infrastructure as code.",
    ),
    (
        "pulse",
        "pulse-bot@pulseboard.app",
        UserRole.MEMBER,
        "I'm Pulse, the PulseBoard AI assistant. Mention me with @pulse!",
    ),
]

# -- Threads and Posts --------------------------------------------------------
# Each entry: (category_slug, author_username, title, body, tag_names, posts)
# Posts: list of (author_username, body, [replies...])
# Replies: list of (author_username, body)
THREADS_DATA = [
    # --- General Discussion ---
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
        ["discussion"],
        [
            (
                "alice",
                "Excited to be here! I'm Alice, a full-stack dev working with React and Python. Looking forward to great discussions.",
                [
                    (
                        "bob",
                        "Welcome Alice! Fellow Python enthusiast here. What frameworks do you use?",
                    ),
                    (
                        "alice",
                        "Mainly FastAPI for the backend and React with Vite on the frontend. You?",
                    ),
                    (
                        "charlie",
                        "React gang! I'm more on the CSS side of things though.",
                    ),
                ],
            ),
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
            (
                "diana",
                "Hello from the data science world! Hoping to learn more about web development here.",
                [],
            ),
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
            (
                "hannah",
                "Love the Reddit-inspired design. Clean and familiar. Nice work!",
                [],
            ),
        ],
    ),
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
    # --- Backend Engineering ---
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
        ["python", "fastapi"],
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
    # --- Frontend Engineering ---
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
        ["css", "react", "discussion"],
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
    # --- DevOps and Deployment ---
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
    # --- Show and Tell ---
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
    # --- Feedback and Suggestions ---
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
        ["css", "feature-request", "bug"],
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
    # --- Off-Topic ---
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
    # --- Help and Support ---
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
CHAT_DATA = [
    # (room_name, room_type, creator_username, member_usernames, messages)
    # messages: list of (sender_username, body)
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
    (
        "DM: alice & bob",
        "direct",
        "alice",
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
    (
        "DM: evan & modmax",
        "direct",
        "evan",
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


def seed() -> None:
    """Populate the database with comprehensive demo data."""
    init_db()
    db = SessionLocal()

    try:
        # Idempotency check: if admin user exists, bail out.
        existing_admin = db.query(User).filter_by(username="admin").first()
        if existing_admin:
            print("[seed] Data already seeded (admin user exists). Skipping.")
            return

        print("[seed] Seeding database with demo data ...")

        # =================================================================
        # 1. USERS
        # =================================================================
        users: dict[str, User] = {}
        for username, email, role, bio in USERS_DATA:
            u = User(
                username=username,
                email=email,
                password_hash=DEMO_HASH,
                role=role,
                bio=bio,
                is_verified=True,
                is_active=True,
                last_seen=_recent(hours_max=72),
            )
            db.add(u)
            users[username] = u

        db.flush()  # assign IDs
        print(f"  - Created {len(users)} users")

        # =================================================================
        # 2. CATEGORIES
        # =================================================================
        categories: dict[str, Category] = {}
        for title, slug, description in CATEGORIES_DATA:
            c = Category(title=title, slug=slug, description=description)
            db.add(c)
            categories[slug] = c

        db.flush()
        print(f"  - Created {len(categories)} categories")

        # =================================================================
        # 3. TAGS
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
        cat_mod_pairs = [
            ("modmax", "general"),
            ("modmax", "backend"),
            ("modsara", "frontend"),
            ("modsara", "devops"),
            ("modmax", "showandtell"),
            ("modsara", "feedback"),
            ("modmax", "offtopic"),
            ("modsara", "help"),
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
        # 5. THREADS, POSTS, THREAD_TAGS, THREAD_SUBSCRIPTIONS, VOTES, REACTIONS
        # =================================================================
        thread_count = 0
        post_count = 0
        vote_count = 0
        reaction_count = 0
        subscription_count = 0

        emojis = ["👍", "❤️", "🔥", "😂", "🎉", "🤔", "👀", "💯"]
        all_usernames = [u for u in users if u != "pulse"]

        for cat_slug, author_uname, title, body, tag_names, posts_data in THREADS_DATA:
            thread_ts = _past(days_ago_max=25)
            thread = Thread(
                category_id=categories[cat_slug].id,
                author_id=users[author_uname].id,
                title=title,
                body=body,
                created_at=thread_ts,
                updated_at=thread_ts,
            )
            db.add(thread)
            db.flush()
            thread_count += 1

            # Tags
            for tname in tag_names:
                if tname in tags:
                    db.add(ThreadTag(thread_id=thread.id, tag_id=tags[tname].id))

            # Thread subscription for author
            db.add(
                ThreadSubscription(
                    thread_id=thread.id,
                    user_id=users[author_uname].id,
                )
            )
            subscription_count += 1

            # Random additional subscriptions
            for uname in random.sample(all_usernames, k=random.randint(2, 6)):
                if uname != author_uname:
                    db.add(
                        ThreadSubscription(
                            thread_id=thread.id,
                            user_id=users[uname].id,
                        )
                    )
                    subscription_count += 1

            # Votes on thread
            for uname in random.sample(all_usernames, k=random.randint(4, 12)):
                value = random.choices([1, -1], weights=[85, 15])[0]
                db.add(
                    Vote(
                        user_id=users[uname].id,
                        entity_type="thread",
                        entity_id=thread.id,
                        value=value,
                    )
                )
                vote_count += 1

            # Reactions on thread
            for uname in random.sample(all_usernames, k=random.randint(1, 4)):
                db.add(
                    Reaction(
                        user_id=users[uname].id,
                        entity_type="thread",
                        entity_id=thread.id,
                        emoji=random.choice(emojis),
                    )
                )
                reaction_count += 1

            # Posts (top-level replies)
            for post_author_uname, post_body, replies_data in posts_data:
                post_ts = thread_ts + timedelta(
                    hours=random.randint(1, 48),
                    minutes=random.randint(0, 59),
                )
                post = Post(
                    thread_id=thread.id,
                    author_id=users[post_author_uname].id,
                    body=post_body,
                    parent_post_id=None,
                    created_at=post_ts,
                    updated_at=post_ts,
                )
                db.add(post)
                db.flush()
                post_count += 1

                # Votes on post
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

                # Reactions on post
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

                # Nested replies
                for reply_author_uname, reply_body in replies_data:
                    reply_ts = post_ts + timedelta(
                        hours=random.randint(1, 24),
                        minutes=random.randint(0, 59),
                    )
                    reply = Post(
                        thread_id=thread.id,
                        author_id=users[reply_author_uname].id,
                        body=reply_body,
                        parent_post_id=post.id,
                        created_at=reply_ts,
                        updated_at=reply_ts,
                    )
                    db.add(reply)
                    db.flush()
                    post_count += 1

                    # Votes on reply
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
        friend_pairs = [
            ("alice", "bob", FriendRequestStatus.ACCEPTED),
            ("alice", "charlie", FriendRequestStatus.ACCEPTED),
            ("alice", "hannah", FriendRequestStatus.ACCEPTED),
            ("bob", "george", FriendRequestStatus.ACCEPTED),
            ("bob", "luna", FriendRequestStatus.ACCEPTED),
            ("charlie", "hannah", FriendRequestStatus.ACCEPTED),
            ("charlie", "julia", FriendRequestStatus.ACCEPTED),
            ("diana", "evan", FriendRequestStatus.ACCEPTED),
            ("diana", "modsara", FriendRequestStatus.ACCEPTED),
            ("fiona", "ivan", FriendRequestStatus.ACCEPTED),
            ("george", "kyle", FriendRequestStatus.ACCEPTED),
            ("modmax", "modsara", FriendRequestStatus.ACCEPTED),
            ("modmax", "admin", FriendRequestStatus.ACCEPTED),
            ("evan", "alice", FriendRequestStatus.PENDING),
            ("julia", "george", FriendRequestStatus.PENDING),
            ("kyle", "fiona", FriendRequestStatus.PENDING),
            ("luna", "diana", FriendRequestStatus.PENDING),
            ("ivan", "charlie", FriendRequestStatus.DECLINED),
        ]
        for requester, recipient, status in friend_pairs:
            fr = FriendRequest(
                requester_id=users[requester].id,
                recipient_id=users[recipient].id,
                status=status,
            )
            if status in (FriendRequestStatus.ACCEPTED, FriendRequestStatus.DECLINED):
                fr.responded_at = _past(days_ago_max=15)
            db.add(fr)

        db.flush()
        print(f"  - Created {len(friend_pairs)} friend requests")

        # =================================================================
        # 7. CHAT ROOMS AND MESSAGES
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
                room_type=room_type,
                created_by_id=users[creator_uname].id,
            )
            db.add(room)
            db.flush()
            room_count += 1

            # Members
            for muname in member_unames:
                db.add(
                    ChatRoomMember(
                        room_id=room.id,
                        user_id=users[muname].id,
                    )
                )

            db.flush()

            # Messages (spread over time)
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
        # A few sample reports for the admin dashboard
        reports_data = [
            (
                "fiona",
                "post",
                3,
                "This post contains misleading information about security practices.",
                "pending",
                None,
            ),
            (
                "hannah",
                "thread",
                18,
                "Title is clickbait / misleading.",
                "pending",
                None,
            ),
            (
                "modmax",
                "post",
                10,
                "Off-topic reply that derails the discussion.",
                "resolved",
                "modmax",
            ),
            (
                "alice",
                "post",
                15,
                "Potential spam / self-promotion.",
                "resolved",
                "modsara",
            ),
            (
                "george",
                "thread",
                5,
                "Duplicate thread, same topic was already discussed.",
                "dismissed",
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
            if resolved_by_uname:
                report.resolved_by = users[resolved_by_uname].id
                report.resolved_at = _past(days_ago_max=5)
            db.add(report)

        db.flush()
        print(f"  - Created {len(reports_data)} content reports")

        # =================================================================
        # 9. MODERATION ACTIONS
        # =================================================================
        mod_actions_data = [
            (
                "modmax",
                "ivan",
                "warn",
                "Off-topic posting in the Backend Engineering category.",
                None,
                None,
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
                    duration_hours=duration,
                    report_id=report_id,
                )
            )

        db.flush()
        print(f"  - Created {len(mod_actions_data)} moderation actions")

        # =================================================================
        # 10. CATEGORY REQUESTS
        # =================================================================
        cat_requests_data = [
            (
                "diana",
                "Machine Learning",
                "ml",
                "Discuss ML models, training, and deployment.",
                "pending",
                None,
            ),
            (
                "ivan",
                "Open Source",
                "opensource",
                "Collaboration on open-source projects.",
                "approved",
                "admin",
            ),
            (
                "julia",
                "Mobile Development",
                "mobile",
                "React Native, Flutter, and native mobile dev.",
                "pending",
                None,
            ),
            (
                "george",
                "System Design",
                "systemdesign",
                "Architecture patterns and system design discussions.",
                "rejected",
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
            if reviewer_uname:
                cr.reviewed_by = users[reviewer_uname].id
                cr.reviewed_at = _past(days_ago_max=5)
            db.add(cr)

        db.flush()
        print(f"  - Created {len(cat_requests_data)} category requests")

        # =================================================================
        # 11. NOTIFICATIONS
        # =================================================================
        notif_data = [
            (
                "alice",
                "reply",
                "New reply to your thread",
                {"thread_id": 1, "post_id": 1, "from_user": "bob"},
            ),
            (
                "alice",
                "reply",
                "New reply to your thread",
                {"thread_id": 4, "post_id": 8, "from_user": "george"},
            ),
            (
                "bob",
                "mention",
                "You were mentioned in a post",
                {"thread_id": 11, "post_id": 25, "from_user": "alice"},
            ),
            (
                "charlie",
                "friend_request",
                "New friend request",
                {"from_user": "ivan", "request_id": 18},
            ),
            (
                "evan",
                "reply",
                "modmax replied to your post",
                {"thread_id": 20, "post_id": 42, "from_user": "modmax"},
            ),
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
            (
                "hannah",
                "reply",
                "charlie replied to your thread",
                {"thread_id": 9, "post_id": 20, "from_user": "charlie"},
            ),
            (
                "diana",
                "friend_accept",
                "modsara accepted your friend request",
                {"from_user": "modsara"},
            ),
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
            (
                "ivan",
                "mod_warning",
                "You received a warning from modmax",
                {"action_id": 1, "moderator": "modmax"},
            ),
        ]
        for user_uname, notif_type, title, payload in notif_data:
            is_read = random.random() > 0.4  # 60% read
            db.add(
                Notification(
                    user_id=users[user_uname].id,
                    notification_type=notif_type,
                    title=title,
                    payload=payload,
                    is_read=is_read,
                )
            )

        db.flush()
        print(f"  - Created {len(notif_data)} notifications")

        # =================================================================
        # 12. AUDIT LOGS
        # =================================================================
        audit_data = [
            ("admin", "user_register", "user", 1, "Admin account created"),
            ("modmax", "user_register", "user", 2, "Moderator modmax registered"),
            ("modsara", "user_register", "user", 3, "Moderator modsara registered"),
            ("alice", "user_register", "user", 4, "User alice registered"),
            ("bob", "user_register", "user", 5, "User bob registered"),
            ("admin", "user_role_change", "user", 2, "Changed role to moderator"),
            ("admin", "user_role_change", "user", 3, "Changed role to moderator"),
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
            ("modmax", "mod_action", "user", 12, "Warned ivan for off-topic posting"),
            ("alice", "user_profile_update", "user", 4, "Updated bio"),
            ("bob", "user_profile_update", "user", 5, "Updated bio"),
            ("admin", "chat_room_create", "chat_room", 1, "Created General Chat room"),
            ("alice", "chat_room_create", "chat_room", 2, "Created Backend Dev room"),
            (
                "charlie",
                "chat_room_create",
                "chat_room",
                3,
                "Created Frontend Dev room",
            ),
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
                    ip_address=f"192.168.1.{random.randint(10, 250)}",
                )
            )

        db.flush()
        print(f"  - Created {len(audit_data)} audit log entries")

        # =================================================================
        # 13. PIN the welcome thread, lock the guidelines thread
        # =================================================================
        # Thread 1 = Welcome, Thread 3 = Community Guidelines
        welcome_thread = (
            db.query(Thread).filter_by(title="Welcome to PulseBoard!").first()
        )
        if welcome_thread:
            welcome_thread.is_pinned = True

        guidelines_thread = (
            db.query(Thread)
            .filter_by(title="Community Guidelines - Please Read")
            .first()
        )
        if guidelines_thread:
            guidelines_thread.is_pinned = True
            guidelines_thread.is_locked = True

        # =================================================================
        # COMMIT
        # =================================================================
        db.commit()
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
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
