#!/usr/bin/env python3
"""Turn pip-outdated.json / npm-outdated.json / docker-outdated.json into a
short markdown summary, and report whether there is anything to do at all."""
import json
import sys


def load(path):
    try:
        with open(path) as fh:
            content = fh.read().strip()
            return json.loads(content) if content else None
    except (FileNotFoundError, ValueError):
        return None


def main():
    pip_path, npm_path, docker_path = sys.argv[1:4]
    lines = []

    pip_data = load(pip_path) or []
    if pip_data:
        lines.append("### Python (backend/requirements.txt)")
        for pkg in pip_data:
            lines.append(f"- `{pkg['name']}` {pkg['version']} -> {pkg['latest_version']}")

    npm_data = load(npm_path) or {}
    if npm_data:
        lines.append("### npm (frontend)")
        for name, info in npm_data.items():
            current = info.get("current", "?")
            latest = info.get("latest", "?")
            lines.append(f"- `{name}` {current} -> {latest}")

    docker_data = load(docker_path) or []
    if docker_data:
        lines.append("### Docker base images")
        for entry in docker_data:
            lines.append(f"- `{entry['image']}` {entry['current']} -> {entry['latest']}")

    summary = "\n".join(lines)
    with open("summary.md", "w") as fh:
        fh.write(summary)
    # Non-empty summary is the only signal the workflow needs.
    print("has updates" if summary.strip() else "no updates", file=sys.stderr)


if __name__ == "__main__":
    main()
