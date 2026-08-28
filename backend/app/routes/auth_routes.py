"""Login / logout / session status."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from .. import auth
from ..models import LoginRequest

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/status")
async def status(request: Request) -> dict:
    token = request.cookies.get(auth.COOKIE_NAME)
    return {
        "auth_required": auth.auth_required(),
        "configured": auth.auth_configured(),
        "authenticated": (not auth.auth_required())
        or auth.session_valid(token),
    }


@router.post("/login")
async def login(req: LoginRequest, request: Request,
                response: Response) -> dict:
    if not auth.auth_required():
        return {"authenticated": True, "note": "authentication is disabled"}

    client = _client(request)
    if auth.throttled(client):
        raise HTTPException(status_code=429,
                            detail="too many failed attempts, try again shortly")

    if not auth.verify_password(req.password):
        auth.record_failure(client)
        raise HTTPException(status_code=401, detail="incorrect password")

    auth.clear_failures(client)
    # Only mark the cookie Secure when the request actually arrived over TLS,
    # otherwise the browser drops it on a plain-HTTP LAN deployment.
    auth.issue_session(response, secure=request.url.scheme == "https")
    return {"authenticated": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    auth.clear_session(response)
    return {"authenticated": False}
