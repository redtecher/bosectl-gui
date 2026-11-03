"""Auto-detect paired BMAP devices (Linux/macOS)."""

import re

# Only a literal MAC is handed on to bluetoothctl, mirroring the C++ guard.
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

import sys

if sys.platform == "darwin":
    from IOBluetooth import IOBluetoothDevice
    from .catalog import lookup_device

    def find_bmap_device():
        """Auto-detect a paired, connected BMAP-capable Bluetooth device on macOS.

        Prioritizes connected devices over paired-but-disconnected ones.
        Returns (mac, device_type) tuple, or (None, None) if not found.
        """
        candidates = []
        try:
            for device in IOBluetoothDevice.pairedDevices():
                pid = device.productID()
                entry = lookup_device(pid)
                if entry and entry.config:
                    # device.getAddressString() returns something like "68-f2-1f-0d-f5-11"
                    candidates.append((device.getAddressString(), entry.config, device.isConnected()))
        except Exception:
            pass

        # Prefer connected devices
        for mac, device_type, connected in candidates:
            if connected:
                return (mac, device_type)

        # Fall back to first paired BMAP device
        for mac, device_type, connected in candidates:
            return (mac, device_type)

        return (None, None)

elif sys.platform == "win32":
    import ctypes
    import winreg
    from ctypes import wintypes
    from .catalog import BMAP_UUID, lookup_device

    # bthprops.cpl carries the classic Bluetooth device enumeration API
    # (irprops.cpl is its pre-Vista alias).
    try:
        _bthprops = ctypes.WinDLL("bthprops.cpl")
    except OSError:
        _bthprops = ctypes.WinDLL("irprops.cpl")

    class _BTH_ADDRESS(ctypes.Union):
        _fields_ = [
            ("ullLong", ctypes.c_ulonglong),
            ("rgBytes", ctypes.c_ubyte * 6),
        ]

    class _SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, wintypes.WORD) for n in (
            "wYear", "wMonth", "wDayOfWeek", "wDay",
            "wHour", "wMinute", "wSecond", "wMilliseconds",
        )]

    class _BTH_DEVICE_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("Address", _BTH_ADDRESS),
            ("ulClassofDevice", wintypes.ULONG),
            ("fConnected", wintypes.BOOL),
            ("fRemembered", wintypes.BOOL),
            ("fAuthenticated", wintypes.BOOL),
            ("stLastSeen", _SYSTEMTIME),
            ("stLastUsed", _SYSTEMTIME),
            ("szName", wintypes.WCHAR * 248),
        ]

    class _BTH_SEARCH_PARAMS(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("fReturnAuthenticated", wintypes.BOOL),
            ("fReturnRemembered", wintypes.BOOL),
            ("fReturnUnknown", wintypes.BOOL),
            ("fReturnConnected", wintypes.BOOL),
            ("fIssueInquiry", wintypes.BOOL),
            ("cTimeoutMultiplier", ctypes.c_ubyte),
            ("hRadio", wintypes.HANDLE),
        ]

    _bthprops.BluetoothFindFirstDevice.argtypes = [
        ctypes.POINTER(_BTH_SEARCH_PARAMS),
        ctypes.POINTER(_BTH_DEVICE_INFO),
    ]
    _bthprops.BluetoothFindFirstDevice.restype = wintypes.HANDLE
    _bthprops.BluetoothFindNextDevice.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BTH_DEVICE_INFO),
    ]
    _bthprops.BluetoothFindNextDevice.restype = wintypes.BOOL
    _bthprops.BluetoothFindDeviceClose.argtypes = [wintypes.HANDLE]
    _bthprops.BluetoothFindDeviceClose.restype = wintypes.BOOL

    def _enum_paired_devices():
        """List (mac, name, connected, class_of_device) for paired devices.

        Uses the classic BluetoothAPIs enumeration from bthprops.cpl — the
        Windows counterpart of `bluetoothctl devices Paired`.
        """
        params = _BTH_SEARCH_PARAMS()
        params.dwSize = ctypes.sizeof(params)
        params.fReturnAuthenticated = True
        params.fReturnRemembered = False
        params.fReturnUnknown = False
        params.fReturnConnected = True
        params.fIssueInquiry = False
        params.cTimeoutMultiplier = 1
        params.hRadio = None

        info = _BTH_DEVICE_INFO()
        info.dwSize = ctypes.sizeof(info)

        devices = []
        handle = _bthprops.BluetoothFindFirstDevice(
            ctypes.byref(params), ctypes.byref(info))
        if not handle:
            return devices
        try:
            while True:
                # rgBytes are stored little-endian (LSB first), so the
                # display MAC reads them reversed.
                mac = ":".join("%02X" % b for b in reversed(info.Address.rgBytes))
                devices.append((mac, info.szName or "",
                                bool(info.fConnected), info.ulClassofDevice))
                ctypes.memset(ctypes.byref(info), 0, ctypes.sizeof(info))
                info.dwSize = ctypes.sizeof(info)
                if not _bthprops.BluetoothFindNextDevice(
                        handle, ctypes.byref(info)):
                    break
        finally:
            _bthprops.BluetoothFindDeviceClose(handle)
        return devices

    _BTHENUM_PATH = r"SYSTEM\CurrentControlSet\Enum\BTHENUM"
    _PID_RE = re.compile(r"_PID&([0-9A-Fa-f]{4})")
    # Instance children look like  a&bdf78bd&0&E458BCC13984_C00000000 —
    # the 12-hex run is the device address in display byte order.
    _INSTANCE_MAC_RE = re.compile(r"([0-9A-Fa-f]{12})(?:_|$)")

    def _bmap_instances():
        """Map device MAC -> product ID from cached BMAP service devnodes.

        When a BMAP device pairs, Windows parses its SDP record and creates a
        devnode per service instance under Enum\\BTHENUM, keyed by the service
        GUID with the Device ID record's VID/PID folded into the key name:

            {<BMAP service GUID>}_VID&0001009e_PID&4082
                a&bdf78bd&0&E458BCC13984_C00000000

        That key is both the BMAP capability check (its existence means the
        device advertises the BMAP service — the registry twin of the
        BMAP_UUID grep the Linux path runs over `bluetoothctl info`) and the
        product ID source (the Modalias equivalent).
        """
        instances = {}
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BTHENUM_PATH)
        except OSError:
            return instances
        bmap_prefix = "{%s}" % BMAP_UUID.lower()
        try:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                if not name.startswith(bmap_prefix):
                    continue
                pid_match = _PID_RE.search(name)
                if not pid_match:
                    continue
                pid = int(pid_match.group(1), 16)
                try:
                    service_key = winreg.OpenKey(root, name)
                except OSError:
                    continue
                try:
                    sub = 0
                    while True:
                        try:
                            child = winreg.EnumKey(service_key, sub)
                        except OSError:
                            break
                        sub += 1
                        mac_match = _INSTANCE_MAC_RE.search(child)
                        if not mac_match:
                            continue
                        hex12 = mac_match.group(1)
                        mac = ":".join(
                            hex12[i:i + 2].upper() for i in range(0, 12, 2))
                        instances.setdefault(mac, pid)
                finally:
                    service_key.Close()
        finally:
            root.Close()
        return instances

    def find_bmap_device():
        """Auto-detect a paired BMAP-capable Bluetooth device on Windows.

        BMAP capability comes from the cached BMAP service devnode in the
        registry (see _bmap_instances); the model comes from the same key's
        product ID via the catalog, defaulting to qc_ultra2 — same fallback
        as the Linux path's Modalias lookup. Live-enumerated connected
        devices are preferred over remembered ones.

        Returns (mac, device_type) tuple, or (None, None) if not found.
        """
        try:
            instances = _bmap_instances()
        except Exception:
            return (None, None)
        if not instances:
            return (None, None)

        def pick(mac):
            entry = lookup_device(instances[mac])
            return (mac, entry.config if entry and entry.config else "qc_ultra2")

        try:
            devices = _enum_paired_devices()
        except Exception:
            devices = []
        ordered = ([d for d in devices if d[2]] +
                   [d for d in devices if not d[2]])
        for mac, name, connected, cod in ordered:
            if mac in instances:
                return pick(mac)

        # No live device matches (radio off, or enumeration came up empty):
        # fall back to the first cached BMAP instance so the caller gets a
        # clear connect error instead of "no device found".
        return pick(next(iter(instances)))


else:
    import re
    import subprocess
    from .catalog import BMAP_UUID, lookup_device

    def find_bmap_device():
        """Auto-detect a paired, connected BMAP-capable Bluetooth device (Linux).

        Prioritizes connected devices over paired-but-disconnected ones.
        Returns (mac, device_type) tuple, or (None, None) if not found.
        """
        candidates = _scan_paired_devices()

        # Prefer connected devices
        for mac, device_type, connected in candidates:
            if connected:
                return (mac, device_type)

        # Fall back to first paired BMAP device
        for mac, device_type, connected in candidates:
            return (mac, device_type)

        return (None, None)


    def _scan_paired_devices():
        """Scan paired Bluetooth devices for BMAP-capable headphones."""
        candidates = []
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices", "Paired"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split(None, 2)
                if len(parts) < 2:
                    continue
                mac = parts[1]
                if not _MAC_RE.match(mac):
                    continue
                info = subprocess.run(
                    ["bluetoothctl", "info", mac],
                    capture_output=True, text=True, timeout=3,
                )
                info_text = info.stdout

                # Must be an audio device with the BMAP UUID
                is_audio = ("audio-headset" in info_text or "audio-headphones" in info_text)
                has_bmap = BMAP_UUID in info_text
                if not (is_audio and has_bmap):
                    continue

                connected = "Connected: yes" in info_text

                # Determine device type from Modalias product ID
                device_type = _detect_device_type(info_text)

                candidates.append((mac, device_type, connected))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return candidates


    def _detect_device_type(info_text):
        """Extract device type from bluetoothctl info output via catalog lookup."""
        match = re.search(r"Modalias:\s*bluetooth:v[0-9A-Fa-f]{4}p([0-9A-Fa-f]{4})", info_text)
        if match:
            product_id = int(match.group(1), 16)
            entry = lookup_device(product_id)
            if entry and entry.config:
                return entry.config
        return "qc_ultra2"  # default for unknown BMAP devices
