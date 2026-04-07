"""Security headers middleware for FastAPI applications.

Adds standard security headers to every response to mitigate common
web vulnerabilities (XSS, clickjacking, MIME sniffing, etc.).
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-related HTTP headers to all responses."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # Prevent MIME-type sniffing — browsers must respect declared Content-Type
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking — only allow framing from same origin
        response.headers["X-Frame-Options"] = "DENY"

        # Enable browser XSS filter (legacy header, still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Restrict referrer information sent to external sites
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Prevent caching of authenticated responses
        if request.headers.get("Authorization"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        # Content-Security-Policy — restrictive by default
        # Allow images from self and data: URIs (for inline previews),
        # scripts only from self, no inline styles from untrusted sources.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "img-src 'self' data: blob: https:; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        # Restrict browser features
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        return response
