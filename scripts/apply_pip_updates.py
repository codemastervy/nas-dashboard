#!/usr/bin/env python3
"""Bump every package in backend/requirements.txt to the version
pip-outdated.json says is latest. Leaves anything not in that file alone."""
import json
import re

with open("pip-outdated.json") as fh:
    outdated = {p["name"].lower(): p["latest_version"] for p in json.load(fh)}

path = "backend/requirements.txt"
lines = open(path).read().splitlines()
out = []
for line in lines:
    m = re.match(r'^([A-Za-z0-9_.\-\[\]]+)==([\w.]+)$', line.strip())
    if not m:
        out.append(line)
        continue
    name, current = m.groups()
    bare_name = re.sub(r'\[.*\]$', '', name).lower()
    latest = outdated.get(bare_name)
    out.append(f"{name}=={latest}" if latest else line)

open(path, "w").write("\n".join(out) + "\n")
