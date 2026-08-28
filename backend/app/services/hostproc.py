"""Point psutil at the host's /proc.

This module must be imported before anything calls psutil, so the override is
in place for the first sample. Importing it for its side effect is deliberate.

Why this exists
---------------
A container gets its own /proc view. Inside it, /proc/stat and /proc/meminfo
are the *host's* (procfs is not namespaced for those files) but /sys/fs/cgroup
limits are the container's, and any library that prefers cgroup values -- or
any runtime with lxcfs in the way -- will report the container's slice instead
of the machine. Dockerised monitoring tools get this wrong constantly: they
show "2 GB of 2 GB memory used" because they read the cgroup limit.

Binding psutil explicitly to a read-only bind mount of the host's /proc makes
the source of truth unambiguous, and makes it verifiable: the numbers must
match what `top` prints on the host.
"""
from __future__ import annotations

import logging

import psutil

from ..config import HOST_PROC

log = logging.getLogger(__name__)

HOST_PROC_ACTIVE = False


def _looks_like_procfs(path) -> bool:
    return (path / "stat").is_file() and (path / "meminfo").is_file()


def bind_host_proc() -> bool:
    """Repoint psutil at the host procfs. Returns True when it took effect."""
    global HOST_PROC_ACTIVE
    if not hasattr(psutil, "PROCFS_PATH"):
        # Non-Linux (e.g. a developer's Mac). Nothing to do.
        log.info("psutil has no PROCFS_PATH on this platform; using defaults")
        return False
    if not _looks_like_procfs(HOST_PROC):
        log.warning(
            "HOST_PROC=%s is not a procfs mount -- CPU/memory figures may "
            "reflect the container cgroup rather than the host. Mount the "
            "host's /proc there (see docker-compose.yml).",
            HOST_PROC,
        )
        return False
    psutil.PROCFS_PATH = str(HOST_PROC)
    HOST_PROC_ACTIVE = True
    log.info("psutil bound to host procfs at %s", HOST_PROC)
    return True


bind_host_proc()
