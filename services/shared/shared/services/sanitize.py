"""
Input Sanitization Utilities for User-Generated Content
========================================================

INTERVIEW CONTEXT:
    This module is a **defense-in-depth** measure against Cross-Site
    Scripting (XSS) attacks.  It lives in the shared layer because
    EVERY service that accepts user text (Core for profiles, Community
    for threads/posts/chat) must sanitize input before storing it.

USED BY:
    - **Core service**: user registration (username), profile updates (bio)
    - **Community service**: thread titles/bodies, post bodies, chat
      messages, category names/descriptions, report reasons, tag names

WHY NOT ``html.escape()``?
    React (our frontend) already escapes all rendered text via JSX.
    If we ran Python's ``html.escape()`` on the backend, users would
    see double-escaped entities: ``&amp;`` instead of ``&``,
    ``&lt;`` instead of ``<``.  This is a common mistake in full-stack
    apps with React frontends.

    Instead, we **surgically remove only dangerous constructs** — the
    kind that could execute code even if React's escaping were bypassed
    (e.g., through ``dangerouslySetInnerHTML`` or a future bug).

WHAT WE STRIP (aligned with OWASP XSS Prevention Cheat Sheet):
    1. **Dangerous HTML tags** (``<script>``, ``<iframe>``, ``<object>``,
       ``<embed>``, ``<style>``, ``<form>``, ``<base>``, ``<link>``,
       ``<meta>``, ``<applet>``) — both paired and self-closing.
    2. **Dangerous URI schemes** (``javascript:``, ``vbscript:``) —
       these can execute code when used in ``href`` or ``src`` attributes.
    3. **Inline event handlers** (``onerror=``, ``onclick=``, ``onload=``,
       etc.) — any ``on<event>=`` pattern that could run JS.

WHAT WE PRESERVE:
    - Normal text with ``<``, ``>``, ``&``, quotes (React handles these)
    - Code snippets (users can post code examples)
    - ``@mentions`` (processed separately by ``mentions.py``)
    - Markdown-like formatting (rendered by the frontend)

OWASP CONTEXT:
    XSS is consistently in the OWASP Top 10 (A03:2021 — Injection).
    Server-side input sanitization is one layer of defense alongside:
    - Output encoding (React's JSX escaping)
    - Content-Security-Policy headers (set by our SecurityHeadersMiddleware)
    - HTTPOnly cookies (prevents JS from reading session tokens)

Provides functions to strip dangerous HTML/script content from user input
while preserving safe text.  This is a defense-in-depth measure — the
frontend uses React which escapes output by default, but we sanitize on
the backend to protect against direct API usage and stored XSS.

IMPORTANT: We do NOT use ``html.escape()`` here because React already
escapes all rendered text.  Running ``html.escape()`` on the backend would
cause double-escaping (users would see ``&amp;`` instead of ``&``).
Instead we surgically remove only genuinely dangerous constructs:
dangerous HTML tags, dangerous URI schemes, and event handler attributes.
"""

import re

# ---------------------------------------------------------------------------
# Dangerous HTML tags that can execute code or embed external content.
# We strip these tags *and their contents* (e.g. <script>...</script>).
#
# INTERVIEW NOTE: We use a regex with a backreference (``\1``) to match
# the closing tag for the same element we opened.  The ``re.DOTALL`` flag
# makes ``.`` match newlines so multi-line script blocks are caught.
# ---------------------------------------------------------------------------
_DANGEROUS_TAGS_WITH_CONTENT_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|applet|form|base|link|meta)"
    r"[^>]*>.*?</\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Self-closing / unclosed dangerous tags (e.g. <script src=...>, <iframe src=...>)
# These catch tags that don't have a matching closing tag — a common XSS
# technique where the attacker relies on the browser to "auto-close" the tag.
_DANGEROUS_TAGS_SELF_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|applet|form|base|link|meta)"
    r"[^>]*>",
    re.IGNORECASE | re.DOTALL,
)

# Regex to catch javascript: / vbscript: URI schemes (dangerous, strips scheme).
# These can appear in href, src, action, and other URL attributes.
# Example attack: <a href="javascript:alert('xss')">click me</a>
_DANGEROUS_URI_RE = re.compile(r"(javascript|vbscript)\s*:", re.IGNORECASE)

# Regex for inline event handler attributes (onerror=, onclick=, onload=, etc.)
# The ``\b`` word boundary prevents false positives on words like "donation=".
# Example attack: <img src=x onerror="alert('xss')">
_EVENT_HANDLER_RE = re.compile(r"\bon\w+\s*=", re.IGNORECASE)


def sanitize_text(text: str) -> str:
    """Sanitize user-generated text content.

    This is the primary sanitization function used by all Pydantic
    ``field_validator`` decorators across the codebase.

    Processing order matters — we strip paired tags first (removing
    their contents), then orphaned tags, then URI schemes, then
    event handlers.

    Args:
        text: Raw user input string (thread body, post body, chat
            message, bio, tag name, etc.).

    Returns:
        Cleaned string with dangerous constructs removed.  Safe
        characters like ``<``, ``>``, ``&`` are left intact (React
        handles escaping them on render).

    Side effects:
        None — this is a pure function with no I/O.

    Steps:
        1. Strip dangerous HTML tags and their contents (script, style, iframe, etc.).
        2. Remove any remaining dangerous self-closing/unclosed dangerous tags.
        3. Remove dangerous URI schemes (``javascript:``, ``vbscript:``).
        4. Remove inline event handler patterns (``onerror=``, etc.).

    Normal text including ``<``, ``>``, ``&``, quotes, code snippets, and
    ``@mentions`` is preserved as-is.  React's JSX escaping handles safe
    rendering on the frontend.
    """
    if not text:
        return text

    # Step 1: Strip dangerous tags with their contents first (e.g. <script>...</script>)
    # This must come before self-closing tag removal to handle complete blocks.
    cleaned = _DANGEROUS_TAGS_WITH_CONTENT_RE.sub("", text)

    # Step 2: Strip any remaining dangerous tags (self-closing, unclosed)
    # Catches <script src="..."> without a closing </script>.
    cleaned = _DANGEROUS_TAGS_SELF_RE.sub("", cleaned)

    # Step 3: Neutralise dangerous URI schemes
    # Removes the scheme portion so "javascript:alert(1)" becomes "alert(1)"
    # which is harmless without the scheme prefix.
    cleaned = _DANGEROUS_URI_RE.sub("", cleaned)

    # Step 4: Remove event handler patterns
    # Turns 'onerror="alert(1)"' into '="alert(1)"' which is inert.
    cleaned = _EVENT_HANDLER_RE.sub("", cleaned)

    return cleaned


def sanitize_username(username: str) -> str:
    """Sanitize a username to alphanumeric + underscores only.

    INTERVIEW NOTE:
        Usernames are used in @mention patterns (``@username``), URL
        slugs, and database queries.  Restricting to ``[a-zA-Z0-9_]``
        prevents:
        - SQL injection via username fields
        - XSS via username rendering
        - Path traversal in avatar URLs (e.g. ``../../../etc/passwd``)
        - Regex injection in the @mention pattern matcher

    Args:
        username: Raw username string from user registration or update.

    Returns:
        Cleaned username containing only ``[a-zA-Z0-9_]`` characters.

    Side effects:
        None — this is a pure function with no I/O.
    """
    if not username:
        return username
    # Strip whitespace first, then remove all characters that aren't
    # alphanumeric or underscore.
    return re.sub(r"[^a-zA-Z0-9_]", "", username.strip())
