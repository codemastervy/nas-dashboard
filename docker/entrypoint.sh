#!/bin/bash
# Container entrypoint: prepare state, then hand off to supervisor.
set -euo pipefail

: "${ENABLE_NMBD:=true}"
export ENABLE_NMBD

mkdir -p /data /etc/samba/shares.d /var/log/samba /run/samba /mnt/storage

# Samba's state directory is a named volume, so on a first run it is an EMPTY
# directory mounted over the one the package created. Samba does not build its
# own skeleton: without /var/lib/samba/private it fails at startup with
#   "smbd can not open secrets.tdb, error code 13"
# and crash-loops forever. Recreate the layout it expects, every boot, since
# the volume may also have been wiped by hand.
mkdir -p /var/lib/samba/private /var/lib/samba/usershares
# private/ holds the password database and the machine secret. 0700 is what
# the Debian package ships and what Samba expects; anything looser is a leak.
chmod 0700 /var/lib/samba/private
chmod 1770 /var/lib/samba/usershares 2>/dev/null || true

# The generated include must exist before smbd starts, or smbd refuses the
# config outright and nothing comes up.
if [ ! -f /etc/samba/shares.d/dashboard-shares.conf ]; then
    cat > /etc/samba/shares.d/dashboard-shares.conf <<'EOF'
# No shares yet. Nothing is exported until you share a folder from the
# dashboard's file browser.
EOF
fi

# The group every SMB user belongs to, and the group that owns shared folders.
# Fixed gid, NOT `groupadd -r`: shared files on your disks are chgrp'd to this
# group, and those on-disk gids outlive the container. A dynamically allocated
# gid could differ after a rebuild, leaving existing files owned by a group
# that no longer means anything. Must match SMB_GID in services/smbusers.py.
: "${SMB_GID:=2999}"
getent group nasusers >/dev/null || groupadd --gid "$SMB_GID" nasusers \
    || groupadd -r nasusers

# Warn loudly rather than silently reporting the container's own cgroup limits
# as though they were the machine's.
if [ ! -r /host/proc/stat ]; then
    echo "WARNING: /host/proc is not mounted. CPU and memory figures will" >&2
    echo "         describe this container, not the host. See README." >&2
fi

if [ -z "${ADMIN_PASSWORD:-}" ] && [ "${AUTH_ENABLED:-true}" != "false" ]; then
    echo "ERROR: ADMIN_PASSWORD is not set. The dashboard will refuse API" >&2
    echo "       requests until it is. Set it in docker-compose.yml." >&2
fi

# Validate the base config early so a typo surfaces here with a clear message
# instead of as a mysterious smbd crash loop.
if ! testparm --suppress-prompt -s /etc/samba/smb.conf >/dev/null 2>/tmp/testparm.err; then
    echo "ERROR: /etc/samba/smb.conf is not valid:" >&2
    cat /tmp/testparm.err >&2
    exit 1
fi

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
