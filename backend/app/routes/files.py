"""File browser endpoints."""
from __future__ import annotations

import asyncio
import mimetypes
import urllib.parse
from pathlib import Path

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile)
from fastapi.responses import StreamingResponse

from ..auth import require_auth
from ..models import (DeleteRequest, MkdirRequest, RenameRequest,
                      TransferRequest)
from ..services import fs, samba

router = APIRouter(prefix="/api/files", tags=["files"],
                   dependencies=[Depends(require_auth)])


def _wrap(exc: fs.FsError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.get("/volumes")
async def get_volumes() -> dict:
    """Top-level drives, each its own entry -- never a flattened tree."""
    try:
        volumes = await asyncio.to_thread(fs.volumes)
    except fs.FsError as exc:
        raise _wrap(exc)

    # Mark which volumes contain shares, so the UI can badge them.
    shared_paths = {s["path"] for s in samba.list_shares()}
    for volume in volumes:
        volume["has_shares"] = any(p == volume["path"] or
                                   p.startswith(volume["path"] + "/")
                                   for p in shared_paths)
    return {"volumes": volumes}


@router.get("/list")
async def list_directory(path: str = Query(...),
                         show_hidden: bool = Query(False)) -> dict:
    try:
        result = await asyncio.to_thread(fs.listdir, path, show_hidden)
    except fs.FsError as exc:
        raise _wrap(exc)

    # Annotate entries that are themselves shared, so the browser can show it.
    shares = {s["path"]: s for s in samba.list_shares()}
    for entry in result["entries"]:
        share = shares.get(entry["path"])
        if share:
            entry["share"] = {"id": share["id"], "name": share["name"]}
    return result


@router.get("/search")
async def search_files(path: str = Query(...), q: str = Query(...),
                       show_hidden: bool = Query(False)) -> dict:
    try:
        return await asyncio.to_thread(fs.search, path, q, 500, show_hidden)
    except fs.FsError as exc:
        raise _wrap(exc)


@router.post("/mkdir")
async def make_directory(req: MkdirRequest) -> dict:
    try:
        return await asyncio.to_thread(fs.mkdir, req.parent, req.name)
    except fs.FsError as exc:
        raise _wrap(exc)


@router.post("/rename")
async def rename_entry(req: RenameRequest) -> dict:
    try:
        return await asyncio.to_thread(fs.rename, req.path, req.new_name)
    except fs.FsError as exc:
        raise _wrap(exc)


@router.post("/copy")
async def copy_entries(req: TransferRequest) -> dict:
    try:
        return await asyncio.to_thread(fs.transfer, req.sources,
                                       req.destination, False, req.overwrite)
    except fs.FsError as exc:
        raise _wrap(exc)


@router.post("/move")
async def move_entries(req: TransferRequest) -> dict:
    try:
        return await asyncio.to_thread(fs.transfer, req.sources,
                                       req.destination, True, req.overwrite)
    except fs.FsError as exc:
        raise _wrap(exc)


@router.post("/delete")
async def delete_entries(req: DeleteRequest) -> dict:
    try:
        return await asyncio.to_thread(fs.delete, req.paths)
    except fs.FsError as exc:
        raise _wrap(exc)


@router.post("/upload")
async def upload_file(path: str = Form(...), overwrite: bool = Form(False),
                      file: UploadFile = File(...)) -> dict:
    filename = Path(file.filename or "upload").name
    try:
        return await asyncio.to_thread(fs.save_upload, path, filename,
                                       file.file, overwrite)
    except fs.FsError as exc:
        raise _wrap(exc)
    finally:
        await file.close()


@router.get("/download")
async def download_file(path: str = Query(...), inline: bool = Query(False)):
    try:
        node = fs.resolve(path)
    except fs.FsError as exc:
        raise _wrap(exc)
    if node.real.is_dir():
        raise HTTPException(status_code=400,
                            detail="cannot download a folder directly")

    media_type = mimetypes.guess_type(node.real.name)[0] \
        or "application/octet-stream"
    # RFC 5987 encoding so non-ASCII filenames survive the round trip.
    quoted = urllib.parse.quote(node.real.name)
    disposition = "inline" if inline else "attachment"
    return StreamingResponse(
        fs.iter_file(node.real),
        media_type=media_type,
        headers={
            "Content-Disposition":
                f"{disposition}; filename*=UTF-8''{quoted}",
            "Content-Length": str(node.real.stat().st_size),
            "Accept-Ranges": "none",
        },
    )
