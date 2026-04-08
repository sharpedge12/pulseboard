"""
Security headers middleware for FastAPI applications.

WHY THIS FILE EXISTS:
    Modern web security relies on HTTP response headers to instruct browsers
    how to handle content.  Without these headers, browsers fall back to
    permissive defaults that leave the application vulnerable to XSS,
    clickjacking, MIME sniffing, and data leakage attacks.

    This middleware adds a comprehensive set of security headers to EVERY
    HTTP response from PulseBoard's backend services.  It's applied to all
    three services (Gateway, Core, Community) as Starlette middleware.

INTERVIEW TALKING POINTS:
    - Defense in depth: These headers are a SECOND layer of protection.  The
      primary defense is proper input sanitization and output encoding in the
      application code.  Headers provide a safety net if the app-level
      defense has a bug.
    - Not all headers are equally important.  CSP (Content-Security-Policy)
      is the most powerful — it can prevent most XSS attacks even if the
      application has an injection vulnerability.  The others are simpler
      but still valuable.
    - Some headers are "legacy" (X-XSS-Protection) but still worth setting
      because older browsers (IE, older Edge) respect them.
    - Middleware is the right place for this because it applies uniformly
      to ALL responses without requiring every route to remember to set
      headers.

OWASP REFERENCE:
    These headers align with OWASP Secure Headers Project recommendations:
    https://owasp.org/www-project-secure-headers/

SEE ALSO:
    - services/gateway/app/main.py   -- adds this middleware
    - services/core/app/main.py      -- adds this middleware
    - services/community/app/main.py -- adds this middleware
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-related HTTP headers to all responses.

    Implemented as Starlette middleware so it wraps the entire request/response
    cycle.  The headers are added AFTER the route handler produces a response
    but BEFORE it's sent to the client, ensuring every response — including
    error responses — gets the security headers.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Let the route handler process the request first.
        # We add headers to the response AFTER processing, not before.
        response = await call_next(request)

        # ---- X-Content-Type-Options ----
        # ATTACK PREVENTED: MIME-type sniffing.
        # Without this, browsers may "guess" the content type of a response
        # by looking at the bytes.  An attacker could upload a file named
        # "image.jpg" that actually contains JavaScript.  The browser might
        # sniff it as text/html and execute the script.  "nosniff" forces
        # the browser to trust the Content-Type header and not guess.
        response.headers["X-Content-Type-Options"] = "nosniff"

        # ---- X-Frame-Options ----
        # ATTACK PREVENTED: Clickjacking.
        # An attacker could embed PulseBoard in an invisible <iframe> on their
        # malicious site, overlaying fake buttons to trick users into clicking
        # on PulseBoard actions (e.g., "delete my account").  "DENY" prevents
        # ANY site (including PulseBoard itself) from framing these responses.
        # For stricter control, "SAMEORIGIN" would allow same-domain framing.
        # We use DENY because PulseBoard has no legitimate framing use case.
        response.headers["X-Frame-Options"] = "DENY"

        # ---- X-XSS-Protection ----
        # ATTACK PREVENTED: Reflected XSS (in older browsers).
        # This header activates the built-in XSS filter in IE and older Chrome.
        # "1; mode=block" means: if the browser detects a reflected XSS attempt,
        # BLOCK the entire page instead of trying to sanitize it (sanitization
        # can sometimes be bypassed).
        # NOTE: Modern browsers have deprecated this in favor of CSP.  We still
        # set it for defense-in-depth with older browser versions.
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # ---- Referrer-Policy ----
        # ATTACK PREVENTED: Information leakage via the Referer header.
        # When a user clicks a link from PulseBoard to an external site, the
        # browser sends a Referer header containing the full URL they came from.
        # This could leak sensitive info in URLs (e.g., /reset-password?token=abc).
        # "strict-origin-when-cross-origin" means:
        #   - Same-origin requests: send full URL (normal behavior).
        #   - Cross-origin requests: send only the origin (https://pulseboard.app),
        #     not the full path.  Strips tokens, IDs, etc.
        #   - HTTPS -> HTTP downgrade: send nothing (prevent leaking to insecure sites).
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # ---- Cache-Control for authenticated responses ----
        # ATTACK PREVENTED: Sensitive data cached by shared proxies or browser cache.
        # If a response containing user-specific data (profile, notifications,
        # private messages) is cached, another user on the same machine (or a
        # shared proxy) could see it.  We detect authenticated requests by the
        # presence of an Authorization header and set aggressive no-cache directives.
        # WHY only for authenticated requests?  Public content (thread listings,
        # category pages) SHOULD be cacheable for performance.
        if request.headers.get("Authorization"):
            # "no-store": Don't store the response at all (strongest directive).
            # "no-cache": Revalidate with server before using cached version.
            # "must-revalidate": Don't serve stale content even if disconnected.
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            # "Pragma: no-cache" is the HTTP/1.0 equivalent of Cache-Control: no-cache.
            # Still needed because some proxies only understand HTTP/1.0 headers.
            response.headers["Pragma"] = "no-cache"

        # ---- Content-Security-Policy (CSP) ----
        # ATTACK PREVENTED: XSS, data injection, and unauthorized resource loading.
        # CSP is the MOST POWERFUL security header.  It tells the browser exactly
        # which sources of content are allowed.  Even if an attacker injects a
        # <script> tag into the page, the browser will REFUSE to execute it
        # because the script's origin isn't in the CSP whitelist.
        response.headers["Content-Security-Policy"] = (
            # default-src 'self': Only allow resources from the same origin
            # unless overridden by a more specific directive below.
            "default-src 'self'; "
            # img-src: Allow images from same origin, data: URIs (for inline
            # base64 previews), blob: URIs (for client-side image processing),
            # and any HTTPS source (for OAuth avatars from Google/GitHub).
            "img-src 'self' data: blob: https:; "
            # script-src 'self': Only allow scripts from the same origin.
            # Blocks ALL inline scripts and scripts from external CDNs.
            # This is the main XSS defense — injected <script> tags won't run.
            "script-src 'self'; "
            # style-src: Allow styles from same origin + inline styles.
            # 'unsafe-inline' is needed because React and many CSS-in-JS
            # solutions inject inline styles.  A stricter alternative would
            # use nonces, but that requires server-side rendering support.
            "style-src 'self' 'unsafe-inline'; "
            # font-src 'self': Only load fonts from same origin.
            # Prevents attackers from loading malicious font files.
            "font-src 'self'; "
            # connect-src: Controls where fetch/XHR/WebSocket can connect.
            # 'self' for API calls, ws:/wss: for WebSocket connections to
            # the Gateway's real-time endpoints.
            "connect-src 'self' ws: wss:; "
            # frame-ancestors 'none': Like X-Frame-Options: DENY but for CSP.
            # Prevents this page from being embedded in any frame/iframe.
            # This is the modern replacement for X-Frame-Options.
            "frame-ancestors 'none'; "
            # base-uri 'self': Restricts the <base> tag to same-origin URLs.
            # Without this, an attacker could inject <base href="evil.com">
            # and all relative URLs on the page would resolve to evil.com.
            "base-uri 'self'; "
            # form-action 'self': Forms can only submit to same-origin URLs.
            # Prevents an attacker from injecting a form that POSTs credentials
            # to their server.
            "form-action 'self'"
        )

        # ---- Permissions-Policy (formerly Feature-Policy) ----
        # ATTACK PREVENTED: Unauthorized access to sensitive browser APIs.
        # Even if an attacker injects JavaScript, they can't access the camera,
        # microphone, or geolocation — the browser will deny the API call.
        # The empty parentheses `()` mean "no origin is allowed" for each feature.
        # PulseBoard is a discussion forum — it has no reason to access hardware.
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        return response
