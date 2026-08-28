"""Filesystem browsing and file operations.

Path model
----------
The API speaks in *virtual* paths, always of the form `/<volume>/<rest>`, where
`<volume>` is the name of an immediate child of STORAGE_ROOT. The browser never
sees or sends a host path. Every virtual path is resolved to a real path and
then checked for containment before anything touches the disk -- this is the
single chokepoint that stops `../../etc/shadow` and symlink escapes, so all
filesystem access in this app goes through `resolve()`.
"""
from __future__ import annotations

import errno
import logging
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Optional

from ..config import STORAGE_ROOT

log = logging.getLogger(__name__)


class FsError(Exception):
    """A filesystem problem that should become a clean HTTP error."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Resolved:
    virtual: str
    real: Path
    volume: str


def volumes() -> list[dict[str, Any]]:
    """Top-level drives. Each is a separate mount, never a flattened tree."""
    out = []
    try:
        children = sorted(p for p in STORAGE_ROOT.iterdir())
    except OSError as exc:
        raise FsError(f"storage root {STORAGE_ROOT} is not readable: {exc}", 500)

    for child in children:
        if not child.is_dir():
            continue
        entry: dict[str, Any] = {"name": child.name, "path": f"/{child.name}"}
        try:
            usage = shutil.disk_usage(child)
            entry.update(total=usage.total, used=usage.used, free=usage.free)
        except OSError:
            entry.update(total=None, used=None, free=None)
        entry["writable"] = os.access(child, os.W_OK)
        out.append(entry)
    return out


def _normalise(virtual: str) -> PurePosixPath:
    if not virtual or not virtual.startswith("/"):
        virtual = "/" + (virtual or "")
    # PurePosixPath collapses '.' and duplicate slashes but deliberately keeps
    # '..' -- we reject those outright rather than trying to normalise them,
    # because normalising before resolution is exactly where traversal bugs live.
    parts = [p for p in PurePosixPath(virtual).parts if p != "/"]
    if any(p == ".." for p in parts):
        raise FsError("path may not contain '..'", 400)
    if any("\x00" in p for p in parts):
        raise FsError("path contains a null byte", 400)
    return PurePosixPath("/", *parts)


def resolve(virtual: str, must_exist: bool = True) -> Resolved:
    """Turn a virtual path into a real one, refusing anything outside a volume."""
    norm = _normalise(virtual)
    parts = [p for p in norm.parts if p != "/"]
    if not parts:
        raise FsError("a volume must be given", 400)

    volume = parts[0]
    volume_root = STORAGE_ROOT / volume
    # Resolve the volume root itself so a symlinked mount is handled correctly.
    try:
        real_root = volume_root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise FsError(f"no such volume: {volume}", 404)
    if not real_root.is_dir():
        raise FsError(f"no such volume: {volume}", 404)

    candidate = volume_root.joinpath(*parts[1:])
    try:
        # strict=False so we can resolve destinations that don't exist yet
        # (an upload target, a new folder). Containment is checked either way.
        real = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise FsError(f"cannot resolve path: {exc}", 400)

    # The containment check. `is_relative_to` compares the *resolved* paths, so
    # a symlink inside the volume pointing at /etc is caught here.
    if real != real_root and not real.is_relative_to(real_root):
        raise FsError("path escapes its volume", 403)

    if must_exist and not real.exists():
        raise FsError("no such file or directory", 404)

    return Resolved(virtual=str(norm), real=real, volume=volume)


def _kind(entry_stat: os.stat_result) -> str:
    mode = entry_stat.st_mode
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    return "special"


def describe(path: Path, virtual_parent: str, name: Optional[str] = None
             ) -> Optional[dict[str, Any]]:
    name = name or path.name
    try:
        # lstat first: we want to know a symlink is a symlink, and we must not
        # follow a dangling one.
        link_stat = path.lstat()
        is_link = stat.S_ISLNK(link_stat.st_mode)
        target_stat = path.stat() if is_link else link_stat
    except OSError:
        return None

    virtual = str(PurePosixPath(virtual_parent) / name)
    kind = _kind(target_stat)
    return {
        "name": name,
        "path": virtual,
        "type": kind,
        "is_dir": kind == "directory",
        "is_symlink": is_link,
        "size": target_stat.st_size if kind != "directory" else None,
        "modified": target_stat.st_mtime,
        "mode": stat.filemode(target_stat.st_mode),
        "hidden": name.startswith("."),
    }


def listdir(virtual: str, show_hidden: bool = False) -> dict[str, Any]:
    node = resolve(virtual)
    if not node.real.is_dir():
        raise FsError("not a directory", 400)

    entries = []
    try:
        with os.scandir(node.real) as it:
            for entry in it:
                if not show_hidden and entry.name.startswith("."):
                    continue
                described = describe(Path(entry.path), node.virtual, entry.name)
                if described:
                    entries.append(described)
    except PermissionError:
        raise FsError("permission denied", 403)
    except OSError as exc:
        raise FsError(f"cannot list directory: {exc}", 500)

    return {
        "path": node.virtual,
        "volume": node.volume,
        "writable": os.access(node.real, os.W_OK),
        "entries": entries,
    }


def search(virtual: str, query: str, limit: int = 500,
           show_hidden: bool = False) -> dict[str, Any]:
    """Recursive substring search from a starting directory.

    Bounded by `limit` and by not following symlinks, so a loop or a huge tree
    cannot make this run forever.
    """
    node = resolve(virtual)
    if not node.real.is_dir():
        raise FsError("not a directory", 400)
    needle = query.strip().lower()
    if not needle:
        return {"path": node.virtual, "query": query, "entries": [],
                "truncated": False}

    results: list[dict[str, Any]] = []
    truncated = False
    deadline = time.monotonic() + 15.0

    for root, dirnames, filenames in os.walk(node.real, followlinks=False):
        if time.monotonic() > deadline:
            truncated = True
            break
        if not show_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            filenames = [f for f in filenames if not f.startswith(".")]

        rel_root = Path(root).relative_to(node.real)
        virtual_root = str(PurePosixPath(node.virtual) / rel_root) \
            if str(rel_root) != "." else node.virtual

        for name in dirnames + filenames:
            if needle not in name.lower():
                continue
            described = describe(Path(root) / name, virtual_root, name)
            if described:
                results.append(described)
            if len(results) >= limit:
                truncated = True
                break
        if truncated:
            break

    return {"path": node.virtual, "query": query, "entries": results,
            "truncated": truncated}


def mkdir(parent: str, name: str) -> dict[str, Any]:
    _reject_bad_name(name)
    node = resolve(parent)
    target = resolve(str(PurePosixPath(node.virtual) / name), must_exist=False)
    try:
        target.real.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        raise FsError("a file or folder with that name already exists", 409)
    except PermissionError:
        raise FsError("permission denied", 403)
    except OSError as exc:
        raise FsError(f"cannot create folder: {exc}", 500)
    return describe(target.real, node.virtual, name) or {}


def rename(virtual: str, new_name: str) -> dict[str, Any]:
    _reject_bad_name(new_name)
    node = resolve(virtual)
    parent_virtual = str(PurePosixPath(node.virtual).parent)
    target = resolve(str(PurePosixPath(parent_virtual) / new_name),
                     must_exist=False)
    if target.real.exists():
        raise FsError("a file or folder with that name already exists", 409)
    try:
        node.real.rename(target.real)
    except PermissionError:
        raise FsError("permission denied", 403)
    except OSError as exc:
        raise FsError(f"cannot rename: {exc}", 500)
    return describe(target.real, parent_virtual, new_name) or {}


def _reject_bad_name(name: str) -> None:
    if not name or name in {".", ".."}:
        raise FsError("invalid name", 400)
    if "/" in name or "\x00" in name:
        raise FsError("a name may not contain '/'", 400)
    if len(name.encode()) > 255:
        raise FsError("name is too long", 400)


def unique_destination(directory: Path, name: str) -> Path:
    """`report.pdf` -> `report 2.pdf` rather than overwriting."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, dot, suffix = name.partition(".")
    counter = 2
    while True:
        alt = f"{stem} {counter}{dot}{suffix}" if dot else f"{name} {counter}"
        candidate = directory / alt
        if not candidate.exists():
            return candidate
        counter += 1


def transfer(sources: list[str], destination: str, move: bool,
             overwrite: bool = False) -> dict[str, Any]:
    """Copy or move a batch. Partial success is reported, not hidden."""
    dest = resolve(destination)
    if not dest.real.is_dir():
        raise FsError("destination is not a directory", 400)

    done, failed = [], []
    for src_virtual in sources:
        try:
            src = resolve(src_virtual)
            # Moving a directory into itself or its own child would either
            # fail obscurely or eat the tree; refuse it up front.
            if src.real.is_dir() and dest.real.is_relative_to(src.real):
                raise FsError("cannot move a folder into itself", 400)

            target = (dest.real / src.real.name) if overwrite \
                else unique_destination(dest.real, src.real.name)

            if move:
                if target.exists() and overwrite:
                    _remove(target)
                shutil.move(str(src.real), str(target))
            else:
                if src.real.is_dir():
                    if target.exists() and overwrite:
                        _remove(target)
                    shutil.copytree(src.real, target, symlinks=True)
                else:
                    shutil.copy2(src.real, target)
            done.append({"source": src_virtual, "name": target.name})
        except FsError as exc:
            failed.append({"source": src_virtual, "error": exc.message})
        except OSError as exc:
            failed.append({"source": src_virtual,
                           "error": os.strerror(exc.errno) if exc.errno
                           else str(exc)})

    return {"moved" if move else "copied": done, "failed": failed}


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def delete(paths: list[str]) -> dict[str, Any]:
    deleted, failed = [], []
    for virtual in paths:
        try:
            node = resolve(virtual)
            if node.real == (STORAGE_ROOT / node.volume).resolve():
                raise FsError("refusing to delete a volume root", 403)
            _remove(node.real)
            deleted.append(virtual)
        except FsError as exc:
            failed.append({"path": virtual, "error": exc.message})
        except OSError as exc:
            failed.append({"path": virtual,
                           "error": os.strerror(exc.errno) if exc.errno
                           else str(exc)})
    return {"deleted": deleted, "failed": failed}


def save_upload(parent: str, filename: str, stream, overwrite: bool = False
                ) -> dict[str, Any]:
    """Stream an upload to disk without buffering it all in memory."""
    _reject_bad_name(filename)
    node = resolve(parent)
    if not node.real.is_dir():
        raise FsError("upload target is not a directory", 400)

    target = (node.real / filename) if overwrite \
        else unique_destination(node.real, filename)
    # Write to a temp name and rename into place, so an interrupted upload
    # never leaves a half-file that looks complete.
    tmp = target.with_name(f".{target.name}.part")
    written = 0
    try:
        with open(tmp, "wb") as fh:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
        tmp.replace(target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise FsError("the drive is full", 507)
        raise FsError(f"upload failed: {exc}", 500)

    return {"name": target.name,
            "path": str(PurePosixPath(node.virtual) / target.name),
            "size": written}


def iter_file(path: Path, chunk_size: int = 1024 * 512) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            yield chunk
