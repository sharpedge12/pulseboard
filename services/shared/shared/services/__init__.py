"""
Shared Services Layer — Cross-Cutting Utilities for All Microservices
=====================================================================

INTERVIEW CONTEXT:
    In a microservice architecture, certain business logic is needed by
    more than one service.  Rather than duplicating that logic (which
    violates DRY and creates maintenance nightmares), we extract it into
    a **shared library** that every service depends on.

    This ``services/shared/shared/services/`` package is that shared
    library.  It is installed as an editable Python package
    (``pip install -e services/shared``) so that both the **Core** service
    (auth, users, notifications) and the **Community** service (forum,
    chat, moderation) can import from it.

WHY A SHARED LAYER INSTEAD OF INTER-SERVICE API CALLS?
    Some operations — like creating a notification row, recording an
    audit log entry, or sanitizing user input — are *in-process* database
    writes that should participate in the **same database transaction** as
    the caller's primary operation.  Making an HTTP call to another
    service would introduce network latency, eventual consistency, and
    partial-failure scenarios that are unnecessary when all services share
    a single PostgreSQL database.

    Rule of thumb: if the operation is a **local DB write** that must be
    transactionally consistent with the caller, put it in the shared
    layer.  If it triggers an independent workflow (e.g. sending a
    WebSocket event), use Redis pub/sub or an HTTP call instead.

WHAT LIVES HERE:
    - ``sanitize.py``    — XSS / input sanitization (defense-in-depth)
    - ``audit.py``       — Audit trail recording & role-based querying
    - ``bot.py``         — @pulse AI assistant (Groq + web search)
    - ``notifications.py`` — In-app notification creation
    - ``mentions.py``    — @mention parsing & notification dispatch
    - ``email.py``       — SMTP email sending (moderation notices)
    - ``moderation.py``  — Moderator scope resolution (category-level)
    - ``attachments.py`` — Two-phase file attachment lifecycle
    - ``storage.py``     — Validated file upload with security hardening

DESIGN PATTERN:
    Each module exposes **pure functions** that accept a SQLAlchemy
    ``Session`` (and other args) and return results.  They do NOT own
    the session lifecycle — the caller (a FastAPI route handler) is
    responsible for committing or rolling back.  This makes the
    functions easy to test, compose, and reason about.
"""
