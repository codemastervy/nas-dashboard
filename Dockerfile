# ---------------------------------------------------------------------------
# Stage 1: build the SPA
# ---------------------------------------------------------------------------
FROM node:26-bookworm-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
# `npm ci` when a lockfile is present (reproducible), `npm install` otherwise.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# samba          - smbd/nmbd, the actual file server
# samba-common-bin - smbpasswd, pdbedit, testparm, smbcontrol
# samba-vfs-modules - fruit/streams_xattr (macOS interop). NOT part of the
#                  `samba` package on Debian: without it EVERY tree connect
#                  fails with NT_STATUS_BAD_NETWORK_NAME, IPC$ included.
# smartmontools  - smartctl for drive health
# supervisor     - runs smbd, nmbd and uvicorn as one container's worth of work
# acl            - setfacl, used when adjusting share directory permissions
RUN apt-get update && apt-get install -y --no-install-recommends \
        samba \
        samba-common-bin \
        samba-vfs-modules \
        smartmontools \
        supervisor \
        acl \
        procps \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/samba/smb.conf

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY docker/smb.conf.base /etc/samba/smb.conf
COPY docker/supervisord.conf /etc/supervisor/conf.d/nas-dashboard.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY --from=frontend /build/dist ./frontend

ENV FRONTEND_DIR=/app/frontend \
    STORAGE_ROOT=/mnt/storage \
    DATA_DIR=/data \
    HOST_PROC=/host/proc \
    HOST_SYS=/host/sys \
    SMB_SHARES_DIR=/etc/samba/shares.d

VOLUME ["/data"]
EXPOSE 8080 445 139

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', \
        timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
