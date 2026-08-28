"""Runtime configuration, all of it overridable by environment variable.

The defaults describe how the container is wired by the shipped
docker-compose.yml; nothing here reaches outside those mounts.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Where the host's /proc and /sys are mounted inside the container. Pointing
# psutil at the host procfs is what makes CPU/memory/network numbers reflect
# the machine rather than the container's cgroup limits.
HOST_PROC = Path(os.environ.get("HOST_PROC", "/host/proc"))
HOST_SYS = Path(os.environ.get("HOST_SYS", "/host/sys"))

# Root under which host filesystems are mounted for browsing. Each immediate
# child is presented as one "drive" in the UI.
STORAGE_ROOT = Path(os.environ.get("STORAGE_ROOT", "/mnt/storage"))

# Persistent application state (share registry, user display names, settings).
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

# Samba configuration. The dashboard never edits the main smb.conf; it owns
# exactly one include directory and writes one file per share into it.
SMB_CONF = Path(os.environ.get("SMB_CONF", "/etc/samba/smb.conf"))
SMB_SHARES_DIR = Path(os.environ.get("SMB_SHARES_DIR", "/etc/samba/shares.d"))

# Auth. A single admin password gates the whole UI and API.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
AUTH_ENABLED = _env_bool("AUTH_ENABLED", True)
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(14 * 24 * 3600)))

# Unix ids for SMB accounts. Fixed rather than auto-allocated so that file
# ownership on your disks stays meaningful across container rebuilds. Change
# these if they collide with real groups/users on the HOST -- the on-disk gid
# is what `ls -l` shows on the host, so a collision is cosmetically confusing
# there even though access control inside the container is unaffected.
SMB_GID = int(os.environ.get("SMB_GID", "2999"))
SMB_UID_BASE = int(os.environ.get("SMB_UID_BASE", "3000"))

# Polling / sampling
STATS_INTERVAL = float(os.environ.get("STATS_INTERVAL", "2.0"))

# Feature toggles for things that need extra privilege. When the privilege
# isn't there we degrade to "unavailable" rather than failing the dashboard.
ENABLE_SMART = _env_bool("ENABLE_SMART", True)
ENABLE_GPU = _env_bool("ENABLE_GPU", True)
