"""Samba user management.

Each dashboard user is a real Samba account in the tdbsam passdb, backed by a
Unix account that exists only so Samba has a uid to map to -- the accounts are
created with no home directory and `nologin`, so an SMB credential can never
become a shell login. Display names live in the app's own JSON store, since
Samba has nowhere sensible to keep them.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from ..config import SMB_GID, SMB_UID_BASE
from .shellout import have, run
from .store import users_store

log = logging.getLogger(__name__)

# Unix group that owns shared content. Every SMB user joins it, which is how
# a share directory can be made group-writable for exactly these users.
SMB_GROUP = "nasusers"

# Stable, explicitly-assigned ids.
#
# This matters more than it looks. The container's /etc/passwd is NOT part of
# any volume, so it is thrown away on every rebuild -- while Samba's passdb
# (in /var/lib/samba) and this app's registry (in /data) both persist. Letting
# the system allocate uids would mean:
#   * after a rebuild the Unix accounts are gone entirely, so every SMB login
#     fails with NT_STATUS_LOGON_FAILURE even though the password is right; and
#   * once recreated, uids could differ, so files already on the disk would be
#     owned by a stranger.
# So ids are allocated from a fixed range, recorded in the registry, and
# reapplied on every start by reconcile().
UID_BASE = SMB_UID_BASE


class UserError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def ensure_group() -> None:
    """Create the shared group with a fixed gid, so group ownership of files
    already written to disk stays valid across container rebuilds."""
    if not have("groupadd"):
        return
    if run(["getent", "group", SMB_GROUP], timeout=10).ok:
        return
    res = run(["groupadd", "--gid", str(SMB_GID), SMB_GROUP], timeout=10)
    if not res.ok:
        # gid already taken by something else; fall back rather than fail.
        run(["groupadd", "-f", SMB_GROUP], timeout=10)


def _next_uid() -> int:
    """Lowest free uid at or above UID_BASE, per the registry."""
    used = {u.get("uid") for u in users_store.read().get("users", [])
            if isinstance(u.get("uid"), int)}
    uid = UID_BASE
    while uid in used or run(["getent", "passwd", str(uid)], timeout=10).ok:
        uid += 1
    return uid


def _create_unix_account(username: str, uid: int) -> bool:
    """Create the uid-stable Unix account Samba maps onto. No home, no shell."""
    if run(["id", "-u", username], timeout=10).ok:
        run(["usermod", "-aG", SMB_GROUP, username], timeout=15)
        return True
    res = run([
        "useradd", "--no-create-home", "--shell", "/usr/sbin/nologin",
        "--uid", str(uid), "--groups", SMB_GROUP, username,
    ], timeout=20)
    if not res.ok:
        log.error("could not create system account %s: %s", username,
                  res.output[:200])
    return res.ok


def reconcile() -> dict[str, Any]:
    """Rebuild the Unix accounts the registry says should exist.

    Called at startup. The container's /etc/passwd is ephemeral; the registry
    and Samba's passdb are not. Without this, every SMB login breaks after a
    `docker compose up --build` even though nothing about the user changed.
    """
    ensure_group()
    restored, missing = [], []
    data = users_store.read()
    changed = False

    for user in data.get("users", []):
        username = user.get("username")
        if not username:
            continue
        if run(["id", "-u", username], timeout=10).ok:
            continue
        uid = user.get("uid")
        if not isinstance(uid, int):
            # Registry predates uid tracking; assign one now and record it.
            uid = _next_uid()
            user["uid"] = uid
            changed = True
        if _create_unix_account(username, uid):
            restored.append(username)
        else:
            missing.append(username)

    if changed:
        users_store.write(data)

    if restored:
        log.info("restored %d Unix account(s) for SMB users: %s",
                 len(restored), ", ".join(restored))
    if missing:
        log.error("could not restore account(s): %s", ", ".join(missing))
    return {"restored": restored, "failed": missing}


def _samba_accounts() -> list[str]:
    """Usernames present in Samba's passdb -- the real source of truth."""
    if not have("pdbedit"):
        return []
    res = run(["pdbedit", "-L"], timeout=15)
    if not res.ok:
        log.warning("pdbedit -L failed: %s", res.output[:200])
        return []
    names = []
    for line in res.stdout.splitlines():
        # Format: username:uid:Full Name
        name = line.split(":", 1)[0].strip()
        if name:
            names.append(name)
    return names


def _metadata() -> dict[str, dict[str, Any]]:
    data = users_store.read()
    return {u["username"]: u for u in data.get("users", [])}


def list_users() -> list[dict[str, Any]]:
    """Merge Samba's passdb with our display-name metadata.

    Samba is authoritative for existence: a user removed with smbpasswd behind
    our back disappears from the list rather than lingering as a ghost entry.
    """
    meta = _metadata()
    accounts = _samba_accounts()
    out = []
    for name in sorted(accounts):
        info = meta.get(name, {})
        out.append({
            "username": name,
            "display_name": info.get("display_name") or name,
            "created_at": info.get("created_at"),
        })
    return out


def user_exists(username: str) -> bool:
    return username in _samba_accounts()


def create_user(username: str, password: str, display_name: str = "") -> dict[str, Any]:
    if not have("smbpasswd") or not have("useradd"):
        raise UserError("Samba user tools are not available in this container", 500)
    if user_exists(username):
        raise UserError(f"user '{username}' already exists", 409)
    if not password:
        raise UserError("a password is required", 400)

    ensure_group()

    existing_unix = run(["id", "-u", username], timeout=10).ok
    uid = _next_uid()
    if not _create_unix_account(username, uid):
        raise UserError("could not create the underlying system account", 500)
    if existing_unix:
        probe = run(["id", "-u", username], timeout=10)
        if probe.ok and probe.stdout.strip().isdigit():
            uid = int(probe.stdout.strip())

    # -s reads the password twice from stdin, so nothing lands in the process
    # table or in shell history.
    res = run(["smbpasswd", "-a", "-s", username], timeout=20,
              input_text=f"{password}\n{password}\n")
    if not res.ok:
        if not existing_unix:
            run(["userdel", username], timeout=15)  # roll back
        raise UserError(f"could not create Samba user: {res.output[:200]}", 500)

    run(["smbpasswd", "-e", username], timeout=15)  # ensure enabled

    import time
    record = {"username": username, "display_name": display_name or username,
              "uid": uid, "created_at": time.time()}

    def mutate(data):
        data.setdefault("users", [])
        data["users"] = [u for u in data["users"] if u["username"] != username]
        data["users"].append(record)

    users_store.update(mutate)
    log.info("created SMB user %s", username)
    return record


def update_user(username: str, password: Optional[str] = None,
                display_name: Optional[str] = None) -> dict[str, Any]:
    if not user_exists(username):
        raise UserError(f"no such user: {username}", 404)

    if password:
        res = run(["smbpasswd", "-s", username], timeout=20,
                  input_text=f"{password}\n{password}\n")
        if not res.ok:
            raise UserError(f"could not change password: {res.output[:200]}", 500)

    if display_name is not None:
        def mutate(data):
            for user in data.setdefault("users", []):
                if user["username"] == username:
                    user["display_name"] = display_name
                    return
            data["users"].append({"username": username,
                                  "display_name": display_name})
        users_store.update(mutate)

    return {"username": username,
            "display_name": _metadata().get(username, {}).get("display_name",
                                                              username)}


def delete_user(username: str) -> None:
    if not user_exists(username):
        raise UserError(f"no such user: {username}", 404)

    res = run(["smbpasswd", "-x", username], timeout=20)
    if not res.ok:
        raise UserError(f"could not remove Samba user: {res.output[:200]}", 500)
    run(["userdel", username], timeout=20)

    def mutate(data):
        data["users"] = [u for u in data.get("users", [])
                         if u["username"] != username]

    users_store.update(mutate)
    log.info("deleted SMB user %s", username)
