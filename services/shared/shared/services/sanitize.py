"""Input sanitization utilities for user-generated content.

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
# ---------------------------------------------------------------------------
_DANGEROUS_TAGS_WITH_CONTENT_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|applet|form|base|link|meta)"
    r"[^>]*>.*?</\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

# Self-closing / unclosed dangerous tags (e.g. <script src=...>, <iframe src=...>)
_DANGEROUS_TAGS_SELF_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|applet|form|base|link|meta)"
    r"[^>]*>",
    re.IGNORECASE | re.DOTALL,
)

# Regex to catch javascript: / vbscript: URI schemes (dangerous, strips scheme)
_DANGEROUS_URI_RE = re.compile(r"(javascript|vbscript)\s*:", re.IGNORECASE)

# Regex for inline event handler attributes (onerror=, onclick=, onload=, etc.)
_EVENT_HANDLER_RE = re.compile(r"\bon\w+\s*=", re.IGNORECASE)


def sanitize_text(text: str) -> str:
    """Sanitize user-generated text content.

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

    # Strip dangerous tags with their contents first (e.g. <script>...</script>)
    cleaned = _DANGEROUS_TAGS_WITH_CONTENT_RE.sub("", text)

    # Strip any remaining dangerous tags (self-closing, unclosed)
    cleaned = _DANGEROUS_TAGS_SELF_RE.sub("", cleaned)

    # Neutralise dangerous URI schemes
    cleaned = _DANGEROUS_URI_RE.sub("", cleaned)

    # Remove event handler patterns
    cleaned = _EVENT_HANDLER_RE.sub("", cleaned)

    return cleaned


def sanitize_username(username: str) -> str:
    """Sanitize a username to alphanumeric + underscores only.

    Strips leading/trailing whitespace and replaces disallowed characters.
    """
    if not username:
        return username
    # Keep only alphanumeric and underscore
    return re.sub(r"[^a-zA-Z0-9_]", "", username.strip())
