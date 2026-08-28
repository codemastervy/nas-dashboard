"""Session authentication for the dashboard itself.

This app can browse, move and delete everything on every disk, and can hand
out SMB credentials. On a home LAN that is reachable by every guest phone and
every IoT device on the same subnet, so it is gated by default rather than
open by default.

One admin password, supplied by environment variable, exchanged for a signed
session cookie. Deliberately not a user database: the SMB users managed by
this app are for file access, not for administering the NAS.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional

from fastapi import Cookie, HTTPException, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import (ADMIN_PASSWORD, AUTH_ENABLED, SESSION_SECRET,
                     SESSION_TTL_SECONDS)
from .services.store import settings_store

log = logging.getLogger(__name__)

COOKIE_NAME = "nasdash_session"

# Failed-login throttle, per source address. Small and in-memory on purpose:
# it exists to make online password guessing impractical, not to survive a
# restart.
_failures: dict[str, list[float]] = {}
_MAX_FAILURES = 8
_WINDOW = 300.0


def _secret() -> str:
    if SESSION_SECRET:
        return SESSION_SECRET
    # Persist a generated secret so sessions survive a container restart.
    # Without this, every restart would silently log everyone out.
    data = settings_store.read()
    secret = data.get("session_secret")
    if not secret:
        secret = secrets.token_urlsafe(48)
        data["session_secret"] = secret
        settings_store.write(data)
        log.info("generated a session secret and stored it in the data volume")
    return secret


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="nasdash-session")


def auth_configured() -> bool:
    return bool(ADMIN_PASSWORD)


def auth_required() -> bool:
    return AUTH_ENABLED and auth_configured()


def throttled(client: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _failures.get(client, []) if now - t < _WINDOW]
    _failures[client] = recent
    return len(recent) >= _MAX_FAILURES


def record_failure(client: str) -> None:
    _failures.setdefault(client, []).append(time.monotonic())


def clear_failures(client: str) -> None:
    _failures.pop(client, None)


def verify_password(candidate: str) -> bool:
    if not ADMIN_PASSWORD:
        return False
    # Constant-time: a timing oracle on the admin password would be a real
    # weakness on a LAN where an attacker can make thousands of attempts.
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode()).digest(),
        hashlib.sha256(ADMIN_PASSWORD.encode()).digest(),
    )


def issue_session(response: Response, secure: bool = False) -> None:
    token = _serializer().dumps({"sub": "admin", "iat": int(time.time())})
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,          # not readable from JS, so XSS cannot lift it
        samesite="lax",         # blocks cross-site form posts
        secure=secure,          # only over HTTPS when the app knows it has it
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def session_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=SESSION_TTL_SECONDS)
        return True
    except (BadSignature, SignatureExpired):
        return False


async def require_auth(
    nasdash_session: Optional[str] = Cookie(default=None),
) -> None:
    """FastAPI dependency guarding every mutating and data-bearing route."""
    if not auth_required():
        return
    if not session_valid(nasdash_session):
        raise HTTPException(status_code=401, detail="authentication required")
