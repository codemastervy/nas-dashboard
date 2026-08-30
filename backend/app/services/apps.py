"""A local, YAML-configured app launcher, in the spirit of Homer.

Nothing here is added or edited through the UI on purpose -- the source of
truth is one file at DATA_DIR/apps.yml, edited directly on the host. It is
read fresh on every request rather than cached, so an edit takes effect on
the next browser refresh with no container restart needed.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import yaml

from ..config import DATA_DIR

log = logging.getLogger(__name__)

APPS_CONFIG = DATA_DIR / "apps.yml"

EXAMPLE = """\
apps:
  - name: Portainer
    icon: "\U0001F433"
    url: http://192.168.1.10:9000
  - name: Plex
    icon: https://raw.githubusercontent.com/plexinc/pms-docker/master/plex.png
    url: http://192.168.1.10:32400/web
"""


def _validate(entry: Any, index: int) -> Optional[dict[str, str]]:
    if not isinstance(entry, dict):
        log.warning("apps.yml entry %d is not a mapping, skipping", index)
        return None
    name = str(entry.get("name") or "").strip()
    url = str(entry.get("url") or "").strip()
    icon = str(entry.get("icon") or "").strip()
    if not name or not url:
        log.warning("apps.yml entry %d is missing name or url, skipping", index)
        return None
    return {"name": name, "url": url, "icon": icon}


def list_apps() -> dict[str, Any]:
    if not APPS_CONFIG.exists():
        return {"apps": [], "exists": False, "error": None,
                "config_path": str(APPS_CONFIG), "example": EXAMPLE}

    try:
        raw = yaml.safe_load(APPS_CONFIG.read_text())
    except yaml.YAMLError as exc:
        return {"apps": [], "exists": True,
                "error": f"apps.yml is not valid YAML: {exc}",
                "config_path": str(APPS_CONFIG), "example": EXAMPLE}

    entries = raw.get("apps") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        return {"apps": [], "exists": True,
                "error": "apps.yml must have a top-level 'apps:' list",
                "config_path": str(APPS_CONFIG), "example": EXAMPLE}

    apps: list[dict[str, str]] = []
    for i, entry in enumerate(entries):
        validated = _validate(entry, i)
        if validated:
            apps.append(validated)

    return {"apps": apps, "exists": True, "error": None,
            "config_path": str(APPS_CONFIG), "example": EXAMPLE}
