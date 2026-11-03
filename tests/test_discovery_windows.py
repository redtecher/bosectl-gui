"""Tests for the Windows BMAP device discovery.

Runs on Windows only; the registry and enumeration layer is faked, no
Bluetooth hardware needed.
"""

import sys
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Only runs on Windows")

from pybmap import discovery
from pybmap.discovery import find_bmap_device


class FakeKey:
    def __init__(self, children=None):
        self.children = children or {}  # name -> FakeKey

    def Close(self):
        pass


class FakeWinreg:
    """Duck-typed winreg module over FakeKey trees."""

    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, root):
        self.root = root

    def OpenKey(self, parent, subname):
        if isinstance(parent, str):
            if subname.endswith("BTHENUM"):
                return self.root
            raise OSError("no such key")
        child = parent.children.get(subname)
        if child is None:
            raise OSError("no such key")
        return child

    def EnumKey(self, key, index):
        names = list(key.children)
        if index >= len(names):
            raise OSError("no more data")
        return names[index]


BMAP_GUID_KEY = ("{00000000-deca-fade-deca-deafdecacaff}"
                 "_VID&0001009e_PID&4082")


@pytest.fixture
def fake_registry(monkeypatch):
    def install(entries):
        root = FakeKey({name: FakeKey({c: FakeKey() for c in children})
                        for name, children in entries.items()})
        monkeypatch.setattr(discovery, "winreg", FakeWinreg(root))
    return install


def test_bmap_instances_extracts_mac_and_pid(fake_registry):
    fake_registry({
        BMAP_GUID_KEY: ["a&bdf78bd&0&E458BCC13984_C00000000"],
        "{0000110b-0000-1000-8000-00805f9b34fb}_VID&0002054c_PID&0c67":
            ["7&abc&0&702605CAC1CF_C00000000"],
    })
    assert discovery._bmap_instances() == {"E4:58:BC:C1:39:84": 0x4082}


def test_bmap_instances_ignores_non_bmap_keys(fake_registry):
    fake_registry({
        "{0000110b-0000-1000-8000-00805f9b34fb}_VID&0001009e_PID&4082":
            ["7&abc&0&E458BCC13984_C00000000"],
    })
    assert discovery._bmap_instances() == {}


def test_find_prefers_connected_device(monkeypatch):
    # Two paired BMAP units; the connected one wins even though the
    # offline one comes first in the live list.
    offline = ("70:26:05:CA:C1:CF", "Bose QC35", False, 0x240404)
    online = ("E4:58:BC:C1:39:84", "Bose QC Ultra 2 HP", True, 0x2C0404)
    monkeypatch.setattr(discovery, "_bmap_instances", lambda: {
        "70:26:05:CA:C1:CF": 0x400C,
        "E4:58:BC:C1:39:84": 0x4082,
    })
    monkeypatch.setattr(discovery, "_enum_paired_devices",
                        lambda: [offline, online])
    assert find_bmap_device() == ("E4:58:BC:C1:39:84", "qc_ultra2")


def test_find_falls_back_when_live_enum_empty(monkeypatch):
    monkeypatch.setattr(discovery, "_bmap_instances",
                        lambda: {"E4:58:BC:C1:39:84": 0x400C})
    monkeypatch.setattr(discovery, "_enum_paired_devices", lambda: [])
    # PID 0x400C = QC35 → config "qc35"
    assert find_bmap_device() == ("E4:58:BC:C1:39:84", "qc35")


def test_find_defaults_unknown_pid(monkeypatch):
    monkeypatch.setattr(discovery, "_bmap_instances",
                        lambda: {"E4:58:BC:C1:39:84": 0x9999})
    monkeypatch.setattr(discovery, "_enum_paired_devices", lambda: [])
    assert find_bmap_device() == ("E4:58:BC:C1:39:84", "qc_ultra2")


def test_find_no_instances(monkeypatch):
    monkeypatch.setattr(discovery, "_bmap_instances", lambda: {})
    assert find_bmap_device() == (None, None)
