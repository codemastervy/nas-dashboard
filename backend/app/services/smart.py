"""SMART health for physical drives, via smartctl.

Privilege note
--------------
This is the one feature that genuinely cannot be done unprivileged. smartctl
issues ATA/NVMe pass-through ioctls straight to the block device, which needs
both the device node present in the container and CAP_SYS_RAWIO (NVMe also
wants CAP_SYS_ADMIN). Without them every call fails with "Permission denied"
regardless of file mode, so the UI reports the capability as unavailable and
says why rather than showing a drive as healthy on no evidence.

Scan results are cached on disk so the dashboard can show "last scan" without
waking a spun-down disk on every page load.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

from ..config import DATA_DIR, ENABLE_SMART
from .shellout import have, run

log = logging.getLogger(__name__)

CACHE_PATH = DATA_DIR / "smart_cache.json"

# A whole-disk node: sda, nvme0n1, vda. Excludes partitions (sda1, nvme0n1p1).
_DISK_RE = re.compile(r"^(sd[a-z]+|nvme\d+n\d+|vd[a-z]+|hd[a-z]+|mmcblk\d+)$")


def _load_cache() -> dict[str, Any]:
    try:
        return json.loads(CACHE_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=2))
        tmp.replace(CACHE_PATH)
    except OSError as exc:
        log.warning("could not persist SMART cache: %s", exc)


def list_devices() -> list[str]:
    """Whole-disk device nodes visible to this container."""
    found = []
    for node in sorted(Path("/dev").glob("*")):
        if _DISK_RE.match(node.name):
            found.append(str(node))
    return found


def capability() -> dict[str, Any]:
    """Can we actually read SMART data right now, and if not, why not."""
    if not ENABLE_SMART:
        return {"available": False, "reason": "disabled by ENABLE_SMART=false"}
    if not have("smartctl"):
        return {"available": False,
                "reason": "smartctl is not installed in the image"}

    devices = list_devices()
    if not devices:
        return {
            "available": False,
            "reason": "no block devices are exposed to the container -- add "
                      "them under `devices:` in docker-compose.yml",
        }

    probe = run(["smartctl", "-i", "-j", devices[0]], timeout=15,
                ok_codes=tuple(range(0, 256)))
    if "Permission denied" in probe.output or probe.code == 127:
        return {
            "available": False,
            "reason": "smartctl cannot issue pass-through ioctls -- the "
                      "container needs cap_add: [SYS_RAWIO, SYS_ADMIN]",
        }
    return {"available": True, "reason": None}


def _parse(device: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = payload.get("model_name") or payload.get("device", {}).get("name")
    serial = payload.get("serial_number")
    capacity = (payload.get("user_capacity") or {}).get("bytes")

    smart_status = payload.get("smart_status") or {}
    passed = smart_status.get("passed")

    temperature = (payload.get("temperature") or {}).get("current")

    power_on = (payload.get("power_on_time") or {}).get("hours")

    # Attributes that actually predict failure. A drive can "PASS" overall
    # while reallocating sectors, so these are surfaced as a warning state.
    warnings: list[str] = []
    watch = {
        "Reallocated_Sector_Ct": "reallocated sectors",
        "Current_Pending_Sector": "pending sectors",
        "Offline_Uncorrectable": "uncorrectable sectors",
        "Reported_Uncorrect": "reported uncorrectable errors",
    }
    table = (payload.get("ata_smart_attributes") or {}).get("table") or []
    for attr in table:
        label = watch.get(attr.get("name", ""))
        if not label:
            continue
        raw = (attr.get("raw") or {}).get("value")
        if isinstance(raw, int) and raw > 0:
            warnings.append(f"{raw} {label}")

    # NVMe equivalents
    nvme = payload.get("nvme_smart_health_information_log") or {}
    if nvme:
        if nvme.get("critical_warning"):
            warnings.append("NVMe critical warning flag set")
        spare = nvme.get("available_spare")
        threshold = nvme.get("available_spare_threshold")
        if isinstance(spare, int) and isinstance(threshold, int) and spare < threshold:
            warnings.append(f"available spare {spare}% below threshold {threshold}%")
        if power_on is None:
            power_on = nvme.get("power_on_hours")
        if temperature is None:
            temperature = nvme.get("temperature")

    if passed is False:
        status = "fail"
    elif warnings:
        status = "warning"
    elif passed is True:
        status = "pass"
    else:
        status = "unknown"

    return {
        "device": device,
        "model": model,
        "serial": serial,
        "capacity": capacity,
        "status": status,
        "warnings": warnings,
        "temperature_c": temperature,
        "power_on_hours": power_on,
        "rotation_rate": payload.get("rotation_rate"),
        "scanned_at": time.time(),
    }


def scan_device(device: str) -> dict[str, Any]:
    """Run a fresh SMART read against one device."""
    # -H health, -A attributes, -i identity, -j JSON. Exit status is a bitmask,
    # not a simple failure flag -- bit 0/1 mean "command failed", higher bits
    # mean "disk is unwell", which is a successful read of bad news.
    res = run(["smartctl", "-H", "-A", "-i", "-j", device], timeout=45,
              ok_codes=tuple(range(0, 256)))

    payload: dict[str, Any] = {}
    if res.stdout.strip():
        try:
            payload = json.loads(res.stdout)
        except ValueError:
            payload = {}

    if not payload:
        return {"device": device, "status": "unknown", "warnings": [],
                "error": res.output[:400] or "smartctl produced no output",
                "scanned_at": time.time()}

    messages = [m.get("string", "") for m in
                (payload.get("smartctl", {}).get("messages") or [])]
    if res.code & 0b11 and not payload.get("smart_status"):
        return {"device": device, "status": "unknown", "warnings": [],
                "error": "; ".join(messages) or f"smartctl exit {res.code}",
                "scanned_at": time.time()}

    parsed = _parse(device, payload)
    if messages:
        parsed["messages"] = messages
    return parsed


def scan_all(persist: bool = True) -> list[dict[str, Any]]:
    results = [scan_device(dev) for dev in list_devices()]
    if persist and results:
        cache = _load_cache()
        for entry in results:
            cache[entry["device"]] = entry
        _save_cache(cache)
    return results


def cached_report() -> dict[str, Any]:
    """What the dashboard shows on load: last known results, no disk wake-up."""
    cap = capability()
    cache = _load_cache()
    drives = [cache[key] for key in sorted(cache)]

    if cap["available"]:
        # Include devices we can see but have never scanned, so they are not
        # silently missing from the list.
        known = {d.get("device") for d in drives}
        for dev in list_devices():
            if dev not in known:
                drives.append({"device": dev, "status": "unknown",
                               "warnings": [], "scanned_at": None,
                               "error": "not scanned yet"})

    return {
        "available": cap["available"],
        "reason": cap["reason"],
        "drives": sorted(drives, key=lambda d: d.get("device") or ""),
    }
