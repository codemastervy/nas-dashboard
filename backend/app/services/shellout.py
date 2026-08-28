"""Thin wrapper around subprocess with the safety rules this app needs.

Every external command in this codebase goes through here. Two rules hold
throughout: arguments are always passed as a list (never a shell string, so
there is no shell to inject into), and every call has a timeout so a wedged
`smartctl` on a spinning-down disk cannot hang a request forever.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Sequence

log = logging.getLogger(__name__)


@dataclass
class Result:
    ok: bool
    code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def run(
    args: Sequence[str],
    timeout: float = 20.0,
    input_text: Optional[str] = None,
    ok_codes: Sequence[int] = (0,),
) -> Result:
    if not args:
        raise ValueError("no command given")
    if not have(args[0]) and "/" not in args[0]:
        return Result(False, 127, "", f"{args[0]}: not found in this container")
    try:
        proc = subprocess.run(  # noqa: S603 - list form, no shell
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("timeout after %ss: %s", timeout, " ".join(args))
        return Result(False, -1, "", f"timed out after {timeout}s")
    except OSError as exc:
        return Result(False, -1, "", str(exc))

    return Result(
        ok=proc.returncode in ok_codes,
        code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )
