"""A small JSON-file store with atomic writes and a process-wide lock.

Not a database on purpose: the state here is a handful of shares and users,
it must survive a container restart, and it must be readable (and repairable)
by a human with a text editor when something goes wrong at 2am.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from ..config import DATA_DIR

log = logging.getLogger(__name__)

_lock = threading.RLock()


class JsonStore:
    def __init__(self, filename: str, default: Any):
        self.path = DATA_DIR / filename
        self._default = default

    def read(self) -> Any:
        with _lock:
            try:
                return json.loads(self.path.read_text())
            except FileNotFoundError:
                return json.loads(json.dumps(self._default))
            except ValueError as exc:
                # Never silently reset a corrupt file -- keep it aside so the
                # data can be recovered by hand.
                broken = self.path.with_suffix(".corrupt")
                log.error("%s is not valid JSON (%s); moved to %s",
                          self.path, exc, broken)
                try:
                    self.path.replace(broken)
                except OSError:
                    pass
                return json.loads(json.dumps(self._default))

    def write(self, data: Any) -> None:
        with _lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.path)

    def update(self, mutator) -> Any:
        with _lock:
            data = self.read()
            result = mutator(data)
            self.write(data)
            return result


shares_store = JsonStore("shares.json", {"shares": []})
users_store = JsonStore("users.json", {"users": []})
settings_store = JsonStore("settings.json", {})
