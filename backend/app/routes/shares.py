"""SMB share endpoints. Sharing is always an explicit act, never implicit."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_auth
from ..models import ShareCreate, ShareUpdate
from ..services import samba

router = APIRouter(prefix="/api/shares", tags=["shares"],
                   dependencies=[Depends(require_auth)])


def _wrap(exc: samba.ShareError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.get("")
async def get_shares() -> dict:
    return {"shares": samba.list_shares(),
            "status": await asyncio.to_thread(samba.status)}


@router.post("")
async def create_share(req: ShareCreate) -> dict:
    try:
        return await asyncio.to_thread(
            samba.create_share, req.path, req.name,
            [m.model_dump() for m in req.members],
            req.read_only, req.guest_ok, req.comment,
        )
    except samba.ShareError as exc:
        raise _wrap(exc)
    except Exception as exc:  # noqa: BLE001
        from ..services import fs
        if isinstance(exc, fs.FsError):
            raise HTTPException(status_code=exc.status, detail=exc.message)
        raise


@router.patch("/{share_id}")
async def update_share(share_id: str, req: ShareUpdate) -> dict:
    try:
        return await asyncio.to_thread(
            samba.update_share, share_id,
            [m.model_dump() for m in req.members] if req.members is not None
            else None,
            req.read_only, req.guest_ok, req.comment,
        )
    except samba.ShareError as exc:
        raise _wrap(exc)


@router.delete("/{share_id}")
async def delete_share(share_id: str) -> dict:
    try:
        return await asyncio.to_thread(samba.delete_share, share_id)
    except samba.ShareError as exc:
        raise _wrap(exc)


@router.get("/config")
async def get_generated_config() -> dict:
    """The exact smb.conf fragment in force -- visible, not hidden from you."""
    path = samba.GENERATED_CONF
    try:
        content = path.read_text()
    except OSError:
        content = "(not generated yet)"
    return {"path": str(path), "content": content}
