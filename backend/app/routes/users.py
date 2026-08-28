"""SMB user endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import UserCreate, UserUpdate
from ..services import samba, smbusers

router = APIRouter(prefix="/api/users", tags=["users"],
                   dependencies=[Depends(require_auth)])


def _wrap(exc: smbusers.UserError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.get("")
async def get_users() -> dict:
    users = await asyncio.to_thread(smbusers.list_users)
    # Show where each user has access, so revoking is an informed decision.
    shares = samba.list_shares()
    for user in users:
        user["shares"] = [
            {"id": s["id"], "name": s["name"],
             "access": next((m["access"] for m in s.get("members", [])
                             if m["username"] == user["username"]), None)}
            for s in shares
            if any(m["username"] == user["username"]
                   for m in s.get("members", []))
        ]
    return {"users": users}


@router.post("")
async def create_user(req: UserCreate) -> dict:
    try:
        return await asyncio.to_thread(smbusers.create_user, req.username,
                                       req.password, req.display_name)
    except smbusers.UserError as exc:
        raise _wrap(exc)


@router.patch("/{username}")
async def update_user(username: str, req: UserUpdate) -> dict:
    try:
        return await asyncio.to_thread(smbusers.update_user, username,
                                       req.password, req.display_name)
    except smbusers.UserError as exc:
        raise _wrap(exc)


@router.delete("/{username}")
async def delete_user(username: str) -> dict:
    try:
        await asyncio.to_thread(smbusers.delete_user, username)
    except smbusers.UserError as exc:
        raise _wrap(exc)

    # A deleted user must also lose membership of every share, otherwise the
    # config would keep naming an account that no longer exists.
    changed = []
    for share in samba.list_shares():
        members = [m for m in share.get("members", [])
                   if m["username"] != username]
        if len(members) != len(share.get("members", [])):
            await asyncio.to_thread(samba.update_share, share["id"], members)
            changed.append(share["name"])

    return {"deleted": username, "removed_from_shares": changed}
