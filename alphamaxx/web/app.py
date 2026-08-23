"""FastHTML app factory: headers, startup hook, route registration.

Everything is served locally — Chart.js, htmx, etc. are vendored under
static/vendor so no request ever leaves localhost. Asset URLs carry a
content-hash query (?v=…) so browsers never serve a stale stylesheet
after a deploy/redesign.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
from pathlib import Path

from fasthtml.common import Link, Script, fast_app
from starlette.middleware import Middleware

from alphamaxx.config import STATIC_DIR, ensure_private_path, settings
from alphamaxx.web.security import ExactHostMiddleware, LocalRequestGuardMiddleware


def _load_or_create_session_key(path: Path) -> str:
    """Return an owner-only signing key without a world-readable create race."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48))

    if path.is_symlink() or not path.is_file():
        raise RuntimeError("session key path must be a regular file")
    ensure_private_path(path, directory=False)
    if os.name != "nt" and not stat.S_ISREG(path.stat().st_mode):
        raise RuntimeError("session key path must be a regular file")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError("session key file is empty")
    return key


def _asset(path: str) -> str:
    """Versioned URL for a file under static/ (short content hash busts caches)."""
    rel = path.lstrip("/").removeprefix("static/")
    digest = hashlib.sha256((STATIC_DIR / rel).read_bytes()).hexdigest()[:8]
    return f"/{rel}?v={digest}"


_hdrs = (
    Link(rel="stylesheet", href=_asset("style.css")),
    Script(src=_asset("vendor/htmx.min.js")),
    Script(src=_asset("vendor/fasthtml.js")),
    Script(src=_asset("vendor/surreal.js")),
    Script(src=_asset("vendor/css-scope-inline.js")),
    Script(src=_asset("vendor/chart.umd.min.js")),
    Script(src=_asset("app.js")),
)

# default_hdrs=False drops FastHTML's CDN script tags. static_path is scoped
# to static/ (absolute, so launching outside the repo root still works): the
# built-in static route resolves *any* repo file with a web extension against
# this directory, so pointing it at ROOT would serve source and docs too.
ensure_private_path(settings.STATE_DIR, directory=True)
_session_key = settings.STATE_DIR / ".sesskey"
_session_secret = _load_or_create_session_key(_session_key)

app, rt = fast_app(
    hdrs=_hdrs,
    default_hdrs=False,
    live=False,
    secret_key=_session_secret,
    key_fname=str(_session_key),
    same_site="strict",
    middleware=(
        Middleware(
            ExactHostMiddleware,
            allowed_hosts=list(settings.ALLOWED_HOSTS),
        ),
        Middleware(LocalRequestGuardMiddleware),
    ),
    static_path=str(STATIC_DIR),
)


@app.on_event("startup")
async def startup():
    from alphamaxx.log import configure_logging
    configure_logging()
    from alphamaxx.data import init_db
    init_db()
    from alphamaxx.services.price_updater import start_daily_updater
    start_daily_updater()


from alphamaxx.web.routes import register_all
register_all(app, rt)
