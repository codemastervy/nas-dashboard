"""WiFi / Ethernet / virtual classification, against a synthetic sysfs.

No VM has a wireless NIC, so the classifier is pointed at a fabricated
/sys/class/net tree that reproduces what the kernel actually creates:

  * a wireless device has a `phy80211` symlink (modern) or a `wireless`
    directory (legacy);
  * a physical device has a `device` link into the PCI/USB bus;
  * a virtual device (bridge, veth, docker0, tun) resolves under
    /sys/devices/virtual/net/.

The names are deliberately misleading in these fixtures, because interface
names are a convention and classifying on them is the bug being avoided.
"""
import pytest

from app.services import stats


@pytest.fixture()
def fake_sys(tmp_path, monkeypatch):
    root = tmp_path / "sys"
    net = root / "class" / "net"
    devices = root / "devices"
    net.mkdir(parents=True)
    (devices / "virtual" / "net").mkdir(parents=True)
    (devices / "pci0000:00" / "0000:00:1f.6").mkdir(parents=True)

    def physical(name, wireless=False, legacy_wireless=False):
        real = devices / "pci0000:00" / "0000:00:1f.6" / name
        real.mkdir(parents=True, exist_ok=True)
        link = net / name
        link.symlink_to(real)
        (real / "device").mkdir(exist_ok=True)
        if wireless:
            (real / "phy80211").mkdir()
        if legacy_wireless:
            (real / "wireless").mkdir()

    def virtual(name):
        real = devices / "virtual" / "net" / name
        real.mkdir(parents=True, exist_ok=True)
        (net / name).symlink_to(real)

    # Named to defeat name-based guessing on purpose.
    physical("eno1")                            # ordinary ethernet
    physical("enp3s0", wireless=True)           # wifi with an "eth-looking" name
    physical("wlp2s0", legacy_wireless=True)    # wifi, legacy sysfs layout
    physical("wlan9")                           # NOT wireless despite the name
    virtual("docker0")
    virtual("veth1a2b3c")
    virtual("br-lan")

    monkeypatch.setattr(stats, "HOST_SYS", root)
    return root


def test_ethernet_is_detected(fake_sys):
    assert stats._interface_kind("eno1") == "ethernet"


def test_wifi_detected_by_phy80211_not_by_name(fake_sys):
    assert stats._interface_kind("enp3s0") == "wifi"


def test_wifi_detected_by_legacy_wireless_dir(fake_sys):
    assert stats._interface_kind("wlp2s0") == "wifi"


def test_name_that_looks_wireless_but_is_not(fake_sys):
    """`wlan9` has no phy80211, so it must not be called wifi."""
    assert stats._interface_kind("wlan9") == "ethernet"


@pytest.mark.parametrize("name", ["docker0", "veth1a2b3c", "br-lan"])
def test_virtual_interfaces_are_excluded(fake_sys, name):
    assert stats._interface_kind(name) == "virtual"


def test_loopback_is_loopback(fake_sys):
    assert stats._interface_kind("lo") == "loopback"


def test_unknown_interface_defaults_to_virtual(fake_sys):
    """Fail safe: an interface we cannot vouch for is not shown as hardware."""
    assert stats._interface_kind("does-not-exist") == "virtual"
