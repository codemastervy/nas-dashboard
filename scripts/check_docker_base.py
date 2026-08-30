#!/usr/bin/env python3
"""Report the newest Docker Hub tag on the same track as each FROM line.

"Same track" means: matching suffix (e.g. "-slim-bookworm"), AND the same
number of dot-separated version components as what's currently pinned --
so a Dockerfile pinned to the major-only "20-bookworm-slim" gets offered
"26-bookworm-slim", never a fully-specified "26.8.1-bookworm-slim" that
would silently change the pinning granularity as a side effect of a
version bump.
"""
import json
import re
import sys
import urllib.request

FROM_RE = re.compile(r'^FROM\s+([\w.-]+):([\w.-]+)', re.MULTILINE)


def _version_key(v: str) -> tuple:
    return tuple(int(p) for p in v.split("."))


def newest_tag(image: str, current: str) -> str | None:
    m = re.match(r'^([\d.]+)(-.*)?$', current)
    if not m:
        return None
    current_version, suffix = m.group(1), (m.group(2) or "")
    components = len(current_version.split("."))
    pattern = re.compile(r'^(\d+(?:\.\d+){%d})%s$' % (components - 1, re.escape(suffix)))

    best = None
    url = f"https://hub.docker.com/v2/repositories/library/{image}/tags?page_size=100"
    for _ in range(5):  # a few pages is enough for an actively maintained image
        if not url:
            break
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
        for entry in data.get("results", []):
            m2 = pattern.match(entry.get("name", ""))
            if not m2:
                continue
            candidate = m2.group(1)
            if best is None or _version_key(candidate) > _version_key(best):
                best = candidate
        url = data.get("next")
    return f"{best}{suffix}" if best else None


def main() -> None:
    dockerfile = open("Dockerfile").read()
    updates = []
    for image, current in FROM_RE.findall(dockerfile):
        if image not in ("python", "node"):
            continue
        try:
            newest = newest_tag(image, current)
        except Exception as exc:  # noqa: BLE001
            # Visible in the CI log rather than silently reporting "nothing
            # to update" when the real story is "the check itself failed".
            print(f"WARNING: could not check {image}:{current}: {exc}",
                  file=sys.stderr)
            continue
        if newest and newest != current:
            m_new = re.match(r'^([\d.]+)', newest)
            m_cur = re.match(r'^([\d.]+)', current)
            if _version_key(m_new.group(1)) > _version_key(m_cur.group(1)):
                updates.append({"image": image, "current": current, "latest": newest})
    json.dump(updates, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
