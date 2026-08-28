"""GPU detection.

Deliberately tolerant: a NUC may have Intel integrated graphics, an NVIDIA
card, AMD, or nothing readable at all. Nothing here is allowed to fail the
dashboard -- when no GPU is legible the widget reports that plainly instead of
erroring or inventing a number.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from ..config import ENABLE_GPU, HOST_SYS
from .shellout import have, run

log = logging.getLogger(__name__)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return None


def _nvidia() -> list[dict[str, Any]]:
    """NVIDIA via nvidia-smi. Needs the NVIDIA container runtime to be wired in."""
    if not have("nvidia-smi"):
        return []
    res = run([
        "nvidia-smi",
        "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ], timeout=10)
    if not res.ok:
        return []

    gpus = []
    for line in res.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue

        def num(value: str) -> Optional[float]:
            try:
                return float(value)
            except ValueError:
                return None

        gpus.append({
            "vendor": "nvidia",
            "name": parts[0],
            "utilization_percent": num(parts[1]),
            "temperature_c": num(parts[2]),
            "memory_used": int(num(parts[3]) or 0) * 1024 * 1024,
            "memory_total": int(num(parts[4]) or 0) * 1024 * 1024,
            "source": "nvidia-smi",
        })
    return gpus


def _amd() -> list[dict[str, Any]]:
    """AMD via the amdgpu driver's sysfs, which needs no extra tooling."""
    gpus = []
    for card in sorted((HOST_SYS / "class" / "drm").glob("card[0-9]")):
        device = card / "device"
        if not (device / "gpu_busy_percent").exists():
            continue
        busy = _read(device / "gpu_busy_percent")
        temp = None
        for hwmon in (device / "hwmon").glob("hwmon*"):
            raw = _read(hwmon / "temp1_input")
            if raw and raw.lstrip("-").isdigit():
                temp = int(raw) / 1000.0
                break
        gpus.append({
            "vendor": "amd",
            "name": _read(device / "product_name") or f"AMD {card.name}",
            "utilization_percent": float(busy) if busy and busy.isdigit() else None,
            "temperature_c": temp,
            "memory_used": _int_or_none(_read(device / "mem_info_vram_used")),
            "memory_total": _int_or_none(_read(device / "mem_info_vram_total")),
            "source": "sysfs:amdgpu",
        })
    return gpus


def _int_or_none(value: Optional[str]) -> Optional[int]:
    if value and value.isdigit():
        return int(value)
    return None


def _intel() -> list[dict[str, Any]]:
    """Intel integrated graphics.

    Utilisation is the awkward one. `intel_gpu_top` needs CAP_PERFMON (or
    perf_event_paranoid relaxed) and root, which this container does not have
    by default, so we do NOT shell out to it during a poll -- it is slow and
    usually blocked. Instead we report the card's presence and its frequency
    from sysfs, which is unprivileged and honest about what it is.
    """
    gpus = []
    for card in sorted((HOST_SYS / "class" / "drm").glob("card[0-9]")):
        device = card / "device"
        vendor = _read(device / "vendor")
        if vendor != "0x8086":  # Intel PCI vendor id
            continue
        cur = _read(device / "gt_cur_freq_mhz") or _read(device / "gt/gt0/rps_cur_freq_mhz")
        maxf = _read(device / "gt_max_freq_mhz") or _read(device / "gt/gt0/rps_max_freq_mhz")

        utilization = None
        if cur and maxf and cur.isdigit() and maxf.isdigit() and int(maxf) > 0:
            # A frequency ratio is a proxy, not true busy-time. Labelled as such
            # in the UI so it is never mistaken for a real utilisation figure.
            utilization = round(int(cur) / int(maxf) * 100, 1)

        temp = None
        for hwmon in (device / "hwmon").glob("hwmon*"):
            raw = _read(hwmon / "temp1_input")
            if raw and raw.lstrip("-").isdigit():
                temp = int(raw) / 1000.0
                break

        gpus.append({
            "vendor": "intel",
            "name": f"Intel Graphics ({card.name})",
            "utilization_percent": utilization,
            "utilization_is_estimate": utilization is not None,
            "temperature_c": temp,
            "freq_mhz": int(cur) if cur and cur.isdigit() else None,
            "freq_max_mhz": int(maxf) if maxf and maxf.isdigit() else None,
            "source": "sysfs:i915",
        })
    return gpus


def gpu_stats() -> dict[str, Any]:
    if not ENABLE_GPU:
        return {"available": False, "reason": "disabled by ENABLE_GPU=false",
                "gpus": []}

    gpus: list[dict[str, Any]] = []
    for probe in (_nvidia, _amd, _intel):
        try:
            gpus.extend(probe())
        except Exception as exc:  # noqa: BLE001 - a broken probe must not
            log.debug("GPU probe %s failed: %s", probe.__name__, exc)

    if not gpus:
        return {
            "available": False,
            "reason": "no readable GPU found (needs /sys mounted, or "
                      "nvidia-smi via the NVIDIA container runtime)",
            "gpus": [],
        }
    return {"available": True, "gpus": gpus}
