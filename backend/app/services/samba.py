"""On-demand SMB share management.

Design
------
Nothing is shared unless a person explicitly shares it. There is no scan, no
auto-export, no "share everything under /mnt". The registry starts empty and
only grows when someone chooses a folder in the file browser and confirms.

The main smb.conf is never rewritten by this app. It carries exactly one line:

    include = /etc/samba/shares.d/dashboard-shares.conf

and this module owns that one generated file, rebuilding it in full from the
JSON registry on every change. Regenerating beats patching because the file
can never drift out of step with the registry, and a hand-edit to smb.conf
proper is never clobbered.

Every regeneration is validated with `testparm` before it is applied, and
rolled back if it does not parse -- a bad write would otherwise take Samba
down for every existing connection.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..config import SMB_SHARES_DIR, STORAGE_ROOT
from . import fs
from .shellout import have, run
from .smbusers import SMB_GROUP, user_exists
from .store import shares_store

log = logging.getLogger(__name__)

GENERATED_CONF = SMB_SHARES_DIR / "dashboard-shares.conf"

_HEADER = """\
# ---------------------------------------------------------------------------
# GENERATED FILE -- DO NOT EDIT BY HAND
#
# Written by nas-dashboard from its share registry (/data/shares.json).
# Any manual change here is lost the next time a share is added or removed.
# To change a share, use the dashboard's Shares page.
# ---------------------------------------------------------------------------

"""


class ShareError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

def list_shares() -> list[dict[str, Any]]:
    data = shares_store.read()
    return sorted(data.get("shares", []), key=lambda s: s.get("name", ""))


def get_share(share_id: str) -> Optional[dict[str, Any]]:
    for share in list_shares():
        if share["id"] == share_id:
            return share
    return None


def _name_taken(name: str, exclude_id: Optional[str] = None) -> bool:
    lowered = name.lower()
    return any(s["name"].lower() == lowered and s["id"] != exclude_id
               for s in list_shares())


def _reserved(name: str) -> bool:
    # These have defined meanings in Samba; reusing them breaks clients.
    return name.lower() in {"global", "homes", "printers", "print$", "ipc$"}


# --------------------------------------------------------------------------
# Config generation
# --------------------------------------------------------------------------

def _escape(value: str) -> str:
    """smb.conf is line-oriented; a newline would inject a directive."""
    return value.replace("\n", " ").replace("\r", " ").strip()


def _render_share(share: dict[str, Any]) -> str:
    members = share.get("members") or []
    readers = [m["username"] for m in members if m.get("access") == "ro"]
    writers = [m["username"] for m in members if m.get("access") == "rw"]
    everyone = [m["username"] for m in members]

    lines = [f"[{_escape(share['name'])}]"]
    if share.get("comment"):
        lines.append(f"   comment = {_escape(share['comment'])}")
    lines.append(f"   path = {share['real_path']}")
    lines.append("   browseable = yes")

    if share.get("guest_ok"):
        # Guest access is opt-in per share and never the default.
        lines.append("   guest ok = yes")
        lines.append("   public = yes")
    else:
        lines.append("   guest ok = no")
        if everyone:
            # `valid users` is the allow-list: nobody outside it can attach,
            # which is what makes "who has access" in the UI actually binding.
            lines.append(f"   valid users = {' '.join(sorted(everyone))}")
        else:
            # A share with no members and no guest access is intentionally
            # unreachable rather than accidentally world-readable.
            lines.append(f"   valid users = @{SMB_GROUP}")

    if share.get("read_only"):
        lines.append("   read only = yes")
    else:
        # Base the share read-only and grant write per user. With no explicit
        # writers this still yields a working read-write share for members.
        if writers or readers:
            lines.append("   read only = yes")
            if writers:
                lines.append(f"   write list = {' '.join(sorted(writers))}")
        else:
            lines.append("   read only = no")

    lines.append("   create mask = 0664")
    lines.append("   directory mask = 0775")
    lines.append("   force group = " + SMB_GROUP)
    lines.append("   vfs objects = catia fruit streams_xattr")
    # Without this, macOS Finder copies leave AppleDouble turds everywhere.
    lines.append("   fruit:metadata = stream")
    lines.append("   fruit:posix_rename = yes")
    lines.append("")
    return "\n".join(lines)


def render_config(shares: list[dict[str, Any]]) -> str:
    body = "".join(_render_share(s) + "\n" for s in shares)
    if not body:
        body = "# No shares. Nothing is exported until someone shares a folder.\n"
    return _HEADER + body


def _validate(candidate: Path) -> tuple[bool, str]:
    """Ask Samba itself whether the config is acceptable."""
    if not have("testparm"):
        return True, "testparm unavailable; skipped validation"
    res = run(["testparm", "--suppress-prompt", "-s", str(candidate)],
              timeout=20, ok_codes=(0, 1))
    if res.code != 0:
        return False, res.output[:500]
    # testparm reports some problems on stderr while still exiting 0.
    for line in res.stderr.splitlines():
        if re.search(r"^(ERROR|Error)", line.strip()):
            return False, line.strip()
    return True, res.stderr[:300]


def apply_config(shares: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """Regenerate the include file, validate it, then reload Samba.

    Reload rather than restart: `smbcontrol all reload-config` makes running
    smbd processes re-read the config, so clients with an open session keep it.
    A restart would drop every connection, which is exactly what you don't want
    when someone is mid-copy on another share.
    """
    shares = list_shares() if shares is None else shares
    SMB_SHARES_DIR.mkdir(parents=True, exist_ok=True)

    previous = GENERATED_CONF.read_text() if GENERATED_CONF.exists() else None
    rendered = render_config(shares)

    staging = GENERATED_CONF.with_suffix(".conf.staging")
    staging.write_text(rendered)

    # testparm needs a whole config to parse, so validate a throwaway that
    # includes the fragment exactly as the real smb.conf does.
    probe = SMB_SHARES_DIR / ".validate.conf"
    probe.write_text("[global]\n   workgroup = WORKGROUP\n"
                     f"   security = user\ninclude = {staging}\n")
    ok, detail = _validate(probe)
    probe.unlink(missing_ok=True)

    if not ok:
        staging.unlink(missing_ok=True)
        raise ShareError(f"generated Samba config is invalid, not applied: {detail}",
                         500)

    staging.replace(GENERATED_CONF)

    reload_result = reload_samba()
    if not reload_result["ok"] and previous is not None:
        GENERATED_CONF.write_text(previous)
        reload_samba()
        raise ShareError(f"Samba refused the new config, rolled back: "
                         f"{reload_result['detail']}", 500)

    return {"applied": True, "shares": len(shares), "reload": reload_result}


def reload_samba() -> dict[str, Any]:
    if not have("smbcontrol"):
        return {"ok": False, "detail": "smbcontrol not present in this container",
                "method": None}
    res = run(["smbcontrol", "all", "reload-config"], timeout=20)
    if res.ok:
        return {"ok": True, "detail": "reloaded without dropping connections",
                "method": "smbcontrol"}
    # smbd not running yet (first boot, or shares created before start).
    return {"ok": False, "detail": res.output[:300] or f"exit {res.code}",
            "method": "smbcontrol"}


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------

def create_share(virtual_path: str, name: str, members: list[dict[str, Any]],
                 read_only: bool = False, guest_ok: bool = False,
                 comment: str = "") -> dict[str, Any]:
    node = fs.resolve(virtual_path)
    if not node.real.is_dir():
        raise ShareError("only a folder can be shared", 400)

    name = name.strip()
    if _reserved(name):
        raise ShareError(f"'{name}' is a name Samba reserves", 400)
    if _name_taken(name):
        raise ShareError(f"a share called '{name}' already exists", 409)

    for member in members:
        if not user_exists(member["username"]):
            raise ShareError(f"no such user: {member['username']}", 400)

    # The container's path is what smbd will open, since smbd runs here.
    real_path = str(node.real)
    if any(s["real_path"] == real_path for s in list_shares()):
        raise ShareError("that folder is already shared", 409)

    share = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "path": node.virtual,
        "real_path": real_path,
        "members": [{"username": m["username"],
                     "access": m.get("access", "rw")} for m in members],
        "read_only": bool(read_only),
        "guest_ok": bool(guest_ok),
        "comment": comment.strip(),
        "created_at": time.time(),
    }

    _prepare_directory(node.real)

    def mutate(data):
        data.setdefault("shares", []).append(share)

    shares_store.update(mutate)

    try:
        apply_config()
    except ShareError:
        # Do not leave a registry entry describing a share Samba never got.
        def rollback(data):
            data["shares"] = [s for s in data.get("shares", [])
                              if s["id"] != share["id"]]
        shares_store.update(rollback)
        raise

    log.info("shared %s as [%s]", node.virtual, name)
    return share


def _prepare_directory(path: Path) -> None:
    """Make a folder usable over SMB by the nasusers group.

    Best-effort: on a filesystem without Unix permissions (an exFAT USB disk,
    say) chown/chmod simply won't stick, and that is not a reason to refuse
    the share.
    """
    import grp
    import os
    try:
        gid = grp.getgrnam(SMB_GROUP).gr_gid
    except KeyError:
        return
    try:
        os.chown(path, -1, gid)
        mode = path.stat().st_mode
        # setgid so new files inherit the group
        os.chmod(path, (mode & 0o777) | 0o2770 & 0o7775 | 0o770)
    except OSError as exc:
        log.info("could not adjust ownership on %s (%s); sharing anyway",
                 path, exc)


def update_share(share_id: str, members: Optional[list[dict[str, Any]]] = None,
                 read_only: Optional[bool] = None,
                 guest_ok: Optional[bool] = None,
                 comment: Optional[str] = None) -> dict[str, Any]:
    share = get_share(share_id)
    if not share:
        raise ShareError("no such share", 404)

    if members is not None:
        for member in members:
            if not user_exists(member["username"]):
                raise ShareError(f"no such user: {member['username']}", 400)

    def mutate(data):
        for entry in data.get("shares", []):
            if entry["id"] != share_id:
                continue
            if members is not None:
                entry["members"] = [{"username": m["username"],
                                     "access": m.get("access", "rw")}
                                    for m in members]
            if read_only is not None:
                entry["read_only"] = bool(read_only)
            if guest_ok is not None:
                entry["guest_ok"] = bool(guest_ok)
            if comment is not None:
                entry["comment"] = comment.strip()

    shares_store.update(mutate)
    apply_config()
    return get_share(share_id) or {}


def delete_share(share_id: str) -> dict[str, Any]:
    """Remove a share for real: gone from the config and from Samba's memory.

    Deleting the registry entry and regenerating is what actually revokes
    access -- the section disappears from the config Samba reloads, so the
    share stops resolving. The files themselves are never touched.
    """
    share = get_share(share_id)
    if not share:
        raise ShareError("no such share", 404)

    def mutate(data):
        data["shares"] = [s for s in data.get("shares", [])
                          if s["id"] != share_id]

    shares_store.update(mutate)
    apply_config()

    # Close any session still attached to the removed share, so "unshared"
    # means disconnected now and not merely "disconnected next time".
    if have("smbcontrol"):
        run(["smbcontrol", "smbd", "close-share", share["name"]], timeout=15)

    log.info("unshared [%s] (%s)", share["name"], share["path"])
    return {"removed": share["name"], "path": share["path"]}


def share_for_path(virtual_path: str) -> Optional[dict[str, Any]]:
    for share in list_shares():
        if share["path"] == virtual_path:
            return share
    return None


def status() -> dict[str, Any]:
    """Is Samba actually running, and does it agree with our registry?"""
    running = False
    detail = "smbd status unknown"
    if have("smbcontrol"):
        res = run(["smbcontrol", "smbd", "ping"], timeout=10)
        running = res.ok
        detail = "responding" if res.ok else (res.output[:200] or "not responding")

    active_names: list[str] = []
    if have("testparm"):
        res = run(["testparm", "--suppress-prompt", "-s"], timeout=20,
                  ok_codes=(0, 1))
        # Section headers in the effective config are the ground truth for
        # what Samba is really exporting right now.
        active_names = [m for m in re.findall(r"^\[([^\]]+)\]", res.stdout,
                                              re.MULTILINE)
                        if m.lower() not in {"global", "homes", "printers",
                                             "print$"}]

    return {
        "running": running,
        "detail": detail,
        "registry_shares": [s["name"] for s in list_shares()],
        "active_shares": sorted(active_names),
        "config_file": str(GENERATED_CONF),
    }
