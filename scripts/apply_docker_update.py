#!/usr/bin/env python3
"""Rewrite Dockerfile FROM lines using docker-outdated.json."""
import json
import re

with open("docker-outdated.json") as fh:
    updates = {u["image"]: (u["current"], u["latest"]) for u in json.load(fh)}

path = "Dockerfile"
text = open(path).read()
for image, (current, latest) in updates.items():
    text = text.replace(f"FROM {image}:{current}", f"FROM {image}:{latest}")
open(path, "w").write(text)
