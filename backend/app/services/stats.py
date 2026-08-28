"""Live system statistics, read from the host rather than the container.

`hostproc` is imported first and for its side effect: it rebinds psutil to the
host's procfs before any sample is taken.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import hostproc  # noqa: F401  (side effect: bind psutil to host /proc)

import psutil

from ..config import HOST_PROC, HOST_SYS, STORAGE_ROOT

log = logging.getLogger(__name__)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(errors="replace").strip()
    except (OSError, UnicodeError):
        return None


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------

def cpu_stats(per_core: bool = True) -> dict[str, Any]:
    # interval=None -> non-blocking, delta since the previous call. The sampler
    # loop calls this on a fixed cadence so the deltas are meaningful.
    overall = psutil.cpu_percent(interval=None)
    cores = psutil.cpu_percent(interval=None, percpu=True) if per_core else []

    load1, load5, load15 = (0.0, 0.0, 0.0)
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        pass

    freq_mhz = None
    try:
        f = psutil.cpu_freq()
        if f:
            freq_mhz = round(f.current, 0)
    except Exception:  # noqa: BLE001 - cpu_freq is flaky in VMs and on ARM
        pass

    return {
        "percent": round(overall, 1),
        "per_core": [round(c, 1) for c in cores],
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "freq_mhz": freq_mhz,
        "load_avg": [round(load1, 2), round(load5, 2), round(load15, 2)],
        "temperature_c": cpu_temperature(),
    }


def _hwmon_entries() -> list[tuple[str, Path]]:
    """(chip name, hwmon dir) for every hwmon device on the host."""
    base = HOST_SYS / "class" / "hwmon"
    out: list[tuple[str, Path]] = []
    try:
        for entry in sorted(base.iterdir()):
            name = _read(entry / "name") or entry.name
            out.append((name, entry))
    except OSError:
        pass
    return out


def cpu_temperature() -> Optional[float]:
    """Package temperature in Celsius, read straight from the host sysfs.

    psutil.sensors_temperatures() reads /sys directly and has no override
    equivalent to PROCFS_PATH, so inside a container it would find nothing.
    Reading the bind-mounted host /sys ourselves is the only reliable route.
    """
    preferred = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal")
    fallback: Optional[float] = None

    for name, entry in _hwmon_entries():
        for temp_input in sorted(entry.glob("temp*_input")):
            raw = _read(temp_input)
            if raw is None or not raw.lstrip("-").isdigit():
                continue
            celsius = int(raw) / 1000.0
            if not (-40 <= celsius <= 150):
                continue
            label = (_read(temp_input.with_name(
                temp_input.name.replace("_input", "_label"))) or "").lower()
            if name in preferred and ("package" in label or "tctl" in label
                                      or not label):
                return round(celsius, 1)
            if fallback is None:
                fallback = round(celsius, 1)

    # Thermal zones are the fallback on boards with no hwmon coretemp.
    if fallback is None:
        for zone in sorted((HOST_SYS / "class" / "thermal").glob("thermal_zone*")):
            raw = _read(zone / "temp")
            if raw and raw.lstrip("-").isdigit():
                celsius = int(raw) / 1000.0
                if -40 <= celsius <= 150:
                    return round(celsius, 1)
    return fallback


# --------------------------------------------------------------------------
# Memory
# --------------------------------------------------------------------------

def memory_stats() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    return {
        "total": vm.total,
        "used": vm.total - vm.available,   # "used" as `free -h` means it
        "available": vm.available,
        "percent": round((vm.total - vm.available) / vm.total * 100, 1)
        if vm.total else 0.0,
        "cached": getattr(vm, "cached", 0),
        "buffers": getattr(vm, "buffers", 0),
        "swap": {
            "total": sm.total,
            "used": sm.used,
            "percent": round(sm.percent, 1),
        },
    }


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------

def _interface_kind(name: str) -> str:
    """Classify an interface as wifi / ethernet / virtual / loopback.

    The reliable signal is sysfs, not the name: `wlp2s0` and `eno1` are
    conventions, not guarantees, and predictable-naming can produce anything.
    A wireless device has a phy80211 symlink (or the legacy `wireless` dir);
    anything whose device link points into the virtual bus is not real hardware.
    """
    if name == "lo":
        return "loopback"
    net = HOST_SYS / "class" / "net" / name
    if (net / "wireless").exists() or (net / "phy80211").exists():
        return "wifi"
    try:
        # /sys/class/net/<if> is a symlink; virtual devices resolve under
        # /sys/devices/virtual/net/... which is how we spot bridges, docker0,
        # veth pairs, tun/tap and the like.
        if "devices/virtual/net" in str(net.resolve()):
            return "virtual"
    except OSError:
        pass
    if (net / "device").exists():
        return "ethernet"
    return "virtual"


@dataclass
class _NetSample:
    at: float
    per_nic: dict[str, tuple[int, int]] = field(default_factory=dict)


_last_net: Optional[_NetSample] = None


def network_stats() -> dict[str, Any]:
    """Per-interface throughput, with wifi and ethernet kept distinct."""
    global _last_net

    counters = psutil.net_io_counters(pernic=True)
    now = time.monotonic()
    sample = _NetSample(
        at=now,
        per_nic={k: (v.bytes_recv, v.bytes_sent) for k, v in counters.items()},
    )

    stats_by_nic = {}
    try:
        stats_by_nic = psutil.net_if_stats()
    except Exception:  # noqa: BLE001
        pass

    interfaces = []
    for name, io in counters.items():
        kind = _interface_kind(name)
        if kind == "loopback":
            continue

        rx_rate = tx_rate = 0.0
        if _last_net and name in _last_net.per_nic:
            elapsed = now - _last_net.at
            if elapsed > 0:
                prev_rx, prev_tx = _last_net.per_nic[name]
                # Guard against counter resets (interface bounced).
                if io.bytes_recv >= prev_rx:
                    rx_rate = (io.bytes_recv - prev_rx) / elapsed
                if io.bytes_sent >= prev_tx:
                    tx_rate = (io.bytes_sent - prev_tx) / elapsed

        nic = stats_by_nic.get(name)
        interfaces.append({
            "name": name,
            "kind": kind,
            "is_up": bool(nic.isup) if nic else None,
            "speed_mbps": (nic.speed or None) if nic else None,
            "addresses": _addresses_for(name),
            "rx_bytes": io.bytes_recv,
            "tx_bytes": io.bytes_sent,
            "rx_rate": round(rx_rate, 1),
            "tx_rate": round(tx_rate, 1),
        })

    _last_net = sample

    # Physical interfaces first, then by name, so the UI order is stable.
    order = {"ethernet": 0, "wifi": 1, "virtual": 2}
    interfaces.sort(key=lambda i: (order.get(i["kind"], 3), i["name"]))
    return {"interfaces": interfaces}


def _addresses_for(name: str) -> list[str]:
    out = []
    try:
        import socket
        for addr in psutil.net_if_addrs().get(name, []):
            if addr.family in (socket.AF_INET, socket.AF_INET6):
                out.append(addr.address.split("%")[0])
    except Exception:  # noqa: BLE001
        pass
    return out


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def storage_stats() -> dict[str, Any]:
    """Usage for every mounted drive exposed under STORAGE_ROOT.

    Each immediate child of STORAGE_ROOT is one host mount, bind-mounted in by
    docker-compose. statvfs on the child reports the real filesystem's numbers.
    """
    volumes = []
    try:
        children = sorted(p for p in STORAGE_ROOT.iterdir() if p.is_dir())
    except OSError as exc:
        log.warning("cannot list STORAGE_ROOT %s: %s", STORAGE_ROOT, exc)
        children = []

    for child in children:
        try:
            usage = psutil.disk_usage(str(child))
        except OSError:
            continue
        volumes.append({
            "name": child.name,
            "path": f"/{child.name}",
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": round(usage.percent, 1),
            "device": _device_for_mount(child),
        })
    return {"volumes": volumes}


def _device_for_mount(path: Path) -> Optional[str]:
    """Best-effort backing device for a bind-mounted path.

    Matching on st_dev against the container's own mount table is the only
    thing that works reliably through a bind mount, since the host path in
    /proc/mounts is not the path we see.
    """
    try:
        target_dev = os.stat(path).st_dev
    except OSError:
        return None
    try:
        with open("/proc/mounts", "r", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 2:
                    continue
                device, mountpoint = parts[0], parts[1].replace("\\040", " ")
                if not device.startswith("/dev/"):
                    continue
                try:
                    if os.stat(mountpoint).st_dev == target_dev:
                        return device
                except OSError:
                    continue
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------
# Host identity / uptime
# --------------------------------------------------------------------------

def _hostname() -> tuple[str, str]:
    """The host's name, in decreasing order of trustworthiness.

    `/proc/sys/kernel/hostname` is UTS-namespaced, so reading it through the
    /host/proc bind mount still yields the *container's* hostname -- which
    Docker sets to the container id. The bind mount cannot see through a
    namespace. In the shipped configuration this happens to be right anyway,
    because `network_mode: host` makes Docker inherit the host's hostname, but
    it is wrong the moment someone runs this bridged. So take an explicit
    answer whenever one is available.
    """
    override = os.environ.get("HOST_HOSTNAME")
    if override:
        return override.strip(), "HOST_HOSTNAME"

    etc = _read(Path("/host/etc/hostname"))
    if etc:
        return etc.splitlines()[0].strip(), "/host/etc/hostname"

    from_proc = _read(HOST_PROC / "sys" / "kernel" / "hostname")
    if from_proc:
        return from_proc, "uts-namespace"

    return "nas", "fallback"


def host_info() -> dict[str, Any]:
    uptime = None
    raw = _read(HOST_PROC / "uptime")
    if raw:
        try:
            uptime = float(raw.split()[0])
        except (ValueError, IndexError):
            pass
    if uptime is None:
        uptime = max(0.0, time.time() - psutil.boot_time())

    hostname, hostname_source = _hostname()
    kernel = _read(HOST_PROC / "sys" / "kernel" / "osrelease")

    return {
        "hostname": hostname,
        "hostname_source": hostname_source,
        "kernel": kernel,
        "uptime_seconds": int(uptime),
        "host_proc_bound": hostproc.HOST_PROC_ACTIVE,
    }
