"""nas-dashboard -- a self-hosted NAS dashboard.

Serves the JSON API and the built SPA from one process, so the whole thing is
a single container with no reverse proxy required.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import auth
from .config import (ADMIN_PASSWORD, AUTH_ENABLED, DATA_DIR, HOST_PROC,
                     SMB_SHARES_DIR, STORAGE_ROOT)
from .routes import apps as apps_routes
from .routes import auth_routes, files, shares, system, users
from .services import hostproc, samba, smbusers

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("nas-dashboard")

app = FastAPI(
    title="nas-dashboard",
    description="Self-hosted NAS dashboard: file browser, on-demand SMB "
                "sharing, and host system monitoring.",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.include_router(auth_routes.router)
app.include_router(system.router)
app.include_router(files.router)
app.include_router(shares.router)
app.include_router(users.router)
app.include_router(apps_routes.router)


@app.on_event("startup")
async def startup() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SMB_SHARES_DIR.mkdir(parents=True, exist_ok=True)

    if not hostproc.HOST_PROC_ACTIVE:
        log.warning(
            "Host procfs is NOT bound (looked at %s). CPU and memory figures "
            "will describe this container, not the machine. Mount the host's "
            "/proc read-only at %s.", HOST_PROC, HOST_PROC)
    else:
        log.info("Host procfs bound at %s -- stats are host-wide", HOST_PROC)

    if not STORAGE_ROOT.exists():
        log.warning("STORAGE_ROOT %s does not exist; no volumes will appear",
                    STORAGE_ROOT)

    if AUTH_ENABLED and not ADMIN_PASSWORD:
        log.error(
            "ADMIN_PASSWORD is not set and AUTH_ENABLED is true. The API is "
            "REFUSING to serve until you set one -- see the README. To run "
            "deliberately open on a trusted network, set AUTH_ENABLED=false.")
    elif not AUTH_ENABLED:
        log.warning("AUTH_ENABLED=false -- the dashboard is unauthenticated. "
                    "Anything that can reach this port has full access.")

    # The container's /etc/passwd is ephemeral while the registry and Samba's
    # passdb persist, so the Unix accounts behind each SMB user must be
    # recreated on every start or logins break after a rebuild.
    try:
        result = smbusers.reconcile()
        if result["restored"]:
            log.info("restored SMB accounts: %s", ", ".join(result["restored"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not reconcile SMB users at startup: %s", exc)

    # Rebuild the Samba config from the registry at boot, so a fresh container
    # reproduces exactly the shares that were configured before.
    try:
        result = samba.apply_config()
        log.info("Samba config applied: %s share(s)", result["shares"])
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        log.warning("could not apply Samba config at startup: %s", exc)


@app.middleware("http")
async def refuse_when_misconfigured(request: Request, call_next):
    """Fail closed.

    If auth is on but no password was ever set, an open dashboard would be
    handing out the whole filesystem. Serving a clear error is the safe
    outcome; the login page and health check stay reachable so the problem is
    diagnosable.
    """
    if (AUTH_ENABLED and not ADMIN_PASSWORD
            and request.url.path.startswith("/api/")
            and request.url.path not in {"/api/auth/status", "/api/health"}):
        return JSONResponse(
            status_code=503,
            content={"detail": "ADMIN_PASSWORD is not set. Set it in "
                               "docker-compose.yml (or set AUTH_ENABLED=false "
                               "to run without authentication)."},
        )
    return await call_next(request)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "host_proc_bound": hostproc.HOST_PROC_ACTIVE,
        "storage_root_present": STORAGE_ROOT.exists(),
        "auth_required": auth.auth_required(),
    }


# --------------------------------------------------------------------------
# Static SPA
# --------------------------------------------------------------------------

FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/app/frontend"))

if (FRONTEND_DIR / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"),
              name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # Anything not an API route falls through to the SPA so client-side
        # routing survives a hard refresh on a deep link.
        candidate = (FRONTEND_DIR / full_path).resolve()
        if (full_path and candidate.is_file()
                and candidate.is_relative_to(FRONTEND_DIR.resolve())):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
else:
    @app.get("/")
    async def no_frontend() -> dict:
        return {"detail": "frontend build not found; API is at /api/docs"}
