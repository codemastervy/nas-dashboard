"""SMART parsing, against real `smartctl -j` output shapes.

The CI machine (and any VM) has no drive that answers SMART, so the parser is
exercised against captured output instead of live hardware. The fixtures below
are the shapes smartctl 7.x actually emits for SATA and NVMe.
"""
import time

from app.services import smart


def _sata(**overrides):
    payload = {
        "model_name": "WDC WD40EFRX-68N32N0",
        "serial_number": "WD-WCC7K4LKZ1P8",
        "user_capacity": {"bytes": 4000787030016},
        "rotation_rate": 5400,
        "temperature": {"current": 38},
        "power_on_time": {"hours": 26280},
        "smart_status": {"passed": True},
        "ata_smart_attributes": {"table": [
            {"name": "Reallocated_Sector_Ct", "raw": {"value": 0}},
            {"name": "Current_Pending_Sector", "raw": {"value": 0}},
            {"name": "Offline_Uncorrectable", "raw": {"value": 0}},
        ]},
    }
    payload.update(overrides)
    return payload


def test_healthy_sata_drive_passes():
    result = smart._parse("/dev/sda", _sata())
    assert result["status"] == "pass"
    assert result["warnings"] == []
    assert result["model"] == "WDC WD40EFRX-68N32N0"
    assert result["capacity"] == 4000787030016
    assert result["temperature_c"] == 38
    assert result["power_on_hours"] == 26280
    assert result["rotation_rate"] == 5400


def test_failed_smart_status_reports_fail():
    result = smart._parse("/dev/sda", _sata(smart_status={"passed": False}))
    assert result["status"] == "fail"


def test_reallocated_sectors_downgrade_a_passing_drive_to_warning():
    """A drive can report PASS while quietly reallocating sectors.

    Surfacing that as 'warning' rather than 'pass' is the whole point -- it is
    the earliest actionable signal that a disk is on its way out.
    """
    payload = _sata()
    payload["ata_smart_attributes"]["table"][0]["raw"]["value"] = 8
    payload["ata_smart_attributes"]["table"][1]["raw"]["value"] = 2

    result = smart._parse("/dev/sda", payload)
    assert result["status"] == "warning"
    assert "8 reallocated sectors" in result["warnings"]
    assert "2 pending sectors" in result["warnings"]


def test_nvme_health_log_is_understood():
    payload = {
        "model_name": "Samsung SSD 980 PRO 1TB",
        "user_capacity": {"bytes": 1000204886016},
        "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {
            "critical_warning": 0,
            "temperature": 41,
            "available_spare": 100,
            "available_spare_threshold": 10,
            "power_on_hours": 9001,
        },
    }
    result = smart._parse("/dev/nvme0n1", payload)
    assert result["status"] == "pass"
    assert result["temperature_c"] == 41
    assert result["power_on_hours"] == 9001


def test_nvme_spare_below_threshold_warns():
    payload = {
        "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {
            "critical_warning": 0,
            "available_spare": 4,
            "available_spare_threshold": 10,
        },
    }
    result = smart._parse("/dev/nvme0n1", payload)
    assert result["status"] == "warning"
    assert any("available spare 4%" in w for w in result["warnings"])


def test_nvme_critical_warning_flag_warns():
    payload = {
        "smart_status": {"passed": True},
        "nvme_smart_health_information_log": {"critical_warning": 1},
    }
    result = smart._parse("/dev/nvme0n1", payload)
    assert result["status"] == "warning"


def test_absent_smart_status_is_unknown_not_pass():
    """Never claim a drive is healthy on no evidence."""
    result = smart._parse("/dev/sda", {"model_name": "Some Disk"})
    assert result["status"] == "unknown"


def test_disk_regex_matches_whole_disks_only():
    matches = smart._DISK_RE.match
    for good in ("sda", "sdab", "nvme0n1", "vda", "hdb", "mmcblk0"):
        assert matches(good), good
    for bad in ("sda1", "nvme0n1p2", "vda15", "loop0", "dm-0", "sr0", "ram1"):
        assert not matches(bad), bad
