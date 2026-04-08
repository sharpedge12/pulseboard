"""
PulseBoard Shared Schemas — Pydantic Models for API Request/Response
====================================================================

**Interview Concept: What is a "schema" in a web API?**

A schema defines the *shape* of data flowing in and out of your API.  In
FastAPI (and many modern frameworks), schemas serve two critical roles:

1. **Request validation** — When a client sends JSON to the server, the
   schema validates the data *before* your business logic ever runs.  If
   the data is invalid (wrong type, missing field, too long, etc.), the
   framework returns a 422 Unprocessable Entity response automatically.

2. **Response serialization** — When the server sends data back, the
   schema controls which fields are included and how they're formatted.
   This prevents accidentally leaking internal fields (like password
   hashes) to the client.

**Why Pydantic?**

Pydantic is Python's most popular data validation library.  FastAPI is
built on top of it.  Key features used throughout these schemas:

- ``BaseModel`` — The parent class for all schemas.  Provides automatic
  JSON parsing, validation, and serialization.
- ``Field(...)`` — Adds constraints like ``min_length``, ``max_length``,
  ``ge`` (greater-than-or-equal), ``pattern`` (regex), etc.
- ``field_validator`` — Custom validation logic that runs after Pydantic's
  built-in checks.  We use these extensively for XSS sanitization.
- ``ConfigDict(from_attributes=True)`` — Tells Pydantic to read data from
  SQLAlchemy model attributes (not just dicts).  This bridges the ORM
  layer and the API layer.

**Package layout** — Each file in this package groups schemas by domain:

- ``auth.py``         — Registration, login, JWT tokens, OAuth, password reset
- ``user.py``         — User profiles, friend requests, user reports
- ``thread.py``       — Forum thread create/update/list/detail + pagination
- ``post.py``         — Forum post (reply) create/update/response
- ``category.py``     — Forum category (community) schemas
- ``chat.py``         — Real-time chat rooms and messages
- ``vote.py``         — Upvote/downvote, emoji reactions, content reports
- ``tag.py``          — Thread tags (labels)
- ``admin.py``        — Admin dashboard, moderation, audit logs
- ``search.py``       — Full-text search results
- ``notification.py`` — In-app notification responses
- ``upload.py``       — File upload metadata
"""
