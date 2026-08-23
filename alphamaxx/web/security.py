"""Local-app request boundary and browser security headers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_SECURITY_HEADERS = (
    (b"content-security-policy", b"default-src 'self'; img-src 'self' data:; "
     b"style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
     b"connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
     b"base-uri 'none'; form-action 'self'"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"x-permitted-cross-domain-policies", b"none"),
)


def _hostname(value: str) -> str | None:
    """Return a normalized hostname from a Host header/config value."""
    raw = value.strip()
    if not raw or any(char.isspace() for char in raw) or "*" in raw:
        return None
    if raw.count(":") >= 2 and not raw.startswith("["):
        try:
            return str(ipaddress.ip_address(raw)).lower()
        except ValueError:
            return None
    try:
        parsed = urlsplit(raw if "://" in raw else f"//{raw}")
        host = (parsed.hostname or "").lower().rstrip(".")
        # Accessing port validates malformed values such as localhost:notaport.
        _ = parsed.port
    except ValueError:
        return None
    if not host or parsed.username is not None or parsed.password is not None:
        return None
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        return None
    return host


class ExactHostMiddleware:
    """Fail closed unless exactly one Host header names an approved host.

    Starlette's historical ``host.split(':')[0]`` behavior does not handle
    bracketed IPv6 correctly. This implementation also deliberately rejects
    wildcard configuration because a wildcard would reopen DNS rebinding.
    """

    def __init__(self, app, allowed_hosts):
        normalized = {_hostname(value) for value in allowed_hosts}
        if None in normalized or not normalized:
            raise ValueError("allowed hosts must be exact hostnames without wildcards")
        self.app = app
        self.allowed_hosts = normalized

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        host_values = [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.lower() == b"host"
        ]
        if len(host_values) != 1 or _hostname(host_values[0]) not in self.allowed_hosts:
            response = PlainTextResponse("Invalid host header", status_code=400)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _origin(value: str, default_scheme: str | None = None) -> tuple[str, str, int] | None:
    """Normalize an Origin URL or Host header for exact comparison."""
    try:
        parsed = urlsplit(value if "://" in value else f"//{value}")
        scheme = (parsed.scheme or default_scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if (
            scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    return scheme, host, port


class LocalRequestGuardMiddleware:
    """Reject browser requests that did not originate from this local app.

    TrustedHostMiddleware separately prevents DNS-rebinding Host values. This
    layer handles cross-site browser requests to an otherwise valid loopback
    Host. Requests without browser origin headers remain available to local CLI
    clients; they carry no ambient application credentials.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers", [])
        headers = {key.lower(): value for key, value in raw_headers}
        duplicate_security_headers = any(
            sum(key.lower() == target for key, _ in raw_headers) > 1
            for target in (b"origin", b"referer", b"sec-fetch-site")
        )
        site = headers.get(b"sec-fetch-site", b"").decode("latin-1").lower()
        host = headers.get(b"host", b"").decode("latin-1")
        expected = _origin(host, scope.get("scheme", "http"))
        origin_value = headers.get(b"origin", b"").decode("latin-1")
        referer_value = headers.get(b"referer", b"").decode("latin-1")

        blocked = (
            duplicate_security_headers
            or site == "cross-site"
            or bool(site and site not in {"same-origin", "same-site", "none"})
        )
        if origin_value and _origin(origin_value) != expected:
            blocked = True
        if scope.get("method", "GET").upper() not in _SAFE_METHODS:
            if referer_value and _origin(referer_value) != expected:
                blocked = True

        if blocked:
            response = PlainTextResponse("Cross-site request rejected.", status_code=403)
            await response(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                response_headers = message.setdefault("headers", [])
                present = {key.lower() for key, _ in response_headers}
                content_type = next(
                    (value for key, value in response_headers
                     if key.lower() == b"content-type"),
                    b"",
                ).split(b";", 1)[0].strip().lower()
                if (
                    content_type in {b"text/html", b"application/json"}
                    and b"cache-control" not in present
                ):
                    response_headers.append((b"cache-control", b"no-store"))
                response_headers.extend(
                    (key, value) for key, value in _SECURITY_HEADERS if key not in present
                )
            await send(message)

        await self.app(scope, receive, send_with_security_headers)
