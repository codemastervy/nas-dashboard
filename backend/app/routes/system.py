"""System monitoring endpoints."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..auth import require_auth, session_valid, COOKIE_NAME, auth_required
from ..config import STATS_INTERVAL
from ..services import gpu, smart, stats

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


def snapshot() -> dict:
    """One full reading of everything the dashboard shows."""
    return {
        "host": stats.host_info(),
        "cpu": stats.cpu_stats(),
        "memory": stats.memory_stats(),
        "network": stats.network_stats(),
        "storage": stats.storage_stats(),
        "gpu": gpu.gpu_stats(),
    }


@router.get("/stats", dependencies=[Depends(require_auth)])
async def get_stats() -> dict:
    return await asyncio.to_thread(snapshot)


@router.get("/stream")
async def stream_stats(request: Request):
    """Server-sent events, so widgets update without polling or a reload.

    SSE rather than WebSockets: the data only flows one way, it survives
    proxies that mangle upgrades, and the browser reconnects on its own.
    """
    if auth_required() and not session_valid(request.cookies.get(COOKIE_NAME)):
        # EventSource cannot read a 401 body, but the status still reaches
        # the onerror handler, which is enough for the UI to redirect.
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="authentication required")

    async def events():
        # Prime psutil's CPU deltas so the first frame isn't a meaningless 0.
        await asyncio.to_thread(stats.cpu_stats)
        await asyncio.to_thread(stats.network_stats)
        await asyncio.sleep(STATS_INTERVAL)
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.to_thread(snapshot)
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception as exc:  # noqa: BLE001 - one bad sample must not
                log.exception("stats sample failed")            # kill the stream
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(STATS_INTERVAL)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # stops nginx buffering the stream
        },
    )


@router.get("/smart", dependencies=[Depends(require_auth)])
async def get_smart() -> dict:
    return await asyncio.to_thread(smart.cached_report)


@router.post("/smart/scan", dependencies=[Depends(require_auth)])
async def run_smart_scan(device: str | None = None) -> dict:
    cap = smart.capability()
    if not cap["available"]:
        return {"available": False, "reason": cap["reason"], "drives": []}
    if device:
        if device not in smart.list_devices():
            return {"available": True, "error": f"unknown device: {device}",
                    "drives": []}
        result = await asyncio.to_thread(smart.scan_device, device)
        return {"available": True, "drives": [result]}
    drives = await asyncio.to_thread(smart.scan_all)
    return {"available": True, "drives": drives}
