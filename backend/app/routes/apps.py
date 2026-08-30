"""Local app launcher, configured entirely by a YAML file on disk."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..auth import require_auth
from ..services import apps

router = APIRouter(prefix="/api/apps", tags=["apps"],
                   dependencies=[Depends(require_auth)])


@router.get("")
async def get_apps() -> dict:
    return await asyncio.to_thread(apps.list_apps)
