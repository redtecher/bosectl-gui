"""RFCOMM Bluetooth socket transport for BMAP devices."""

import sys
import time
from .errors import BmapConnectionError, BmapTimeoutError

RFCOMM_CHANNEL = 2  # BMAP protocol is always on RFCOMM channel 2


if sys.platform == "darwin":
    import objc
    from IOBluetooth import (
        IOBluetoothDevice,
        IOBluetoothRFCOMMChannel,
        NSObject,
    )
    from Foundation import NSRunLoop, NSDate, NSDefaultRunLoopMode
    from queue import Queue
    from threading import Event

    # Register metadata for openRFCOMMChannelSync to make sure out parameters are handled correctly
    objc.registerMetaDataForSelector(
        b"IOBluetoothDevice",
        b"openRFCOMMChannelSync:withChannelID:delegate:",
        dict(
            arguments={
                2: dict(type=objc._C_PTR + objc._C_ID, type_modifier=objc._C_OUT),
                3: dict(type=objc._C_USHT), # Channel ID
                4: dict(type=objc._C_ID)    # Delegate
            }
        )
    )

    class MacOsRfcommDelegate(NSObject):
        def init(self):
            self = objc.super(MacOsRfcommDelegate, self).init()
            if self is None:
                return None
            self.baseband_connected = False
            self.baseband_status = -1
            self.baseband_event = Event()

            self.channel_connected = False
            self.channel_status = -1
            self.channel_event = Event()

            self.received_queue = Queue()
            self.closed_event = Event()
            return self

        def connectionComplete_status_(self, device, status):
            self.baseband_status = status
            self.baseband_connected = (status == 0)
            self.baseband_event.set()

        def rfcommChannelOpenComplete_status_(self, channel, status):
            self.channel_status = status
            self.channel_connected = (status == 0)
            self.channel_event.set()

        @objc.typedSelector(b"v@:@^vQ")
        def rfcommChannelData_data_length_(self, channel, data_ptr, length):
            try:
                # Convert objc.varlist to python bytes
                chunk = bytes(data_ptr.as_buffer(length))
                self.received_queue.put(chunk)
            except Exception:
                try:
                    chunk = bytes(data_ptr[:length])
                    self.received_queue.put(chunk)
                except Exception:
                    pass

        def rfcommChannelClosed_(self, channel):
            self.closed_event.set()


    class MacOsRfcommTransport:
        """Raw RFCOMM Bluetooth IOBluetooth transport for macOS."""

        def __init__(self, mac, channel=RFCOMM_CHANNEL, timeout=3.0):
            self.mac = mac
            self.channel_id = channel
            self.timeout = timeout
            self.device = None
            self.channel = None
            self.delegate = None

        def connect(self):
            """Open the RFCOMM channel using native IOBluetooth."""
            try:
                self.device = IOBluetoothDevice.deviceWithAddressString_(self.mac)
                if not self.device:
                    raise BmapConnectionError("Device not found: %s" % self.mac)

                self.delegate = MacOsRfcommDelegate.alloc().init()
                run_loop = NSRunLoop.currentRunLoop()

                # 1. Query SDP first so macOS registers active Bluetooth services
                self.device.performSDPQuery_(None)
                start_sdp = time.time()
                while time.time() - start_sdp < 1.5:
                    run_loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))

                # 2. Establish baseband connection asynchronously
                status = self.device.openConnection_(self.delegate)
                if status != 0:
                    raise BmapConnectionError("Failed to issue openConnection (status %d)" % status)

                # Wait up to 5 seconds for baseband connection to establish
                start_bb = time.time()
                while time.time() - start_bb < 5.0 and not self.delegate.baseband_event.is_set():
                    run_loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))

                if not self.delegate.baseband_connected:
                    raise BmapConnectionError(
                        "Failed to establish baseband connection (status %d)" % self.delegate.baseband_status
                    )

                # 3. Open RFCOMM channel directly on the requested channel ID
                status, channel = self.device.openRFCOMMChannelSync_withChannelID_delegate_(
                    None, self.channel_id, self.delegate
                )
                if status != 0:
                    raise BmapConnectionError("Failed to open RFCOMM channel %d (status %d)" % (self.channel_id, status))

                # Wait for channel open complete
                start_ch = time.time()
                while time.time() - start_ch < 3.0 and not self.delegate.channel_event.is_set():
                    run_loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.05))

                if not self.delegate.channel_connected:
                    raise BmapConnectionError("RFCOMM channel open failed (status %d)" % self.delegate.channel_status)

                self.channel = channel

                # 4. Flush any startup handshake/broadcast packets
                time.sleep(0.5)
                while not self.delegate.received_queue.empty():
                    try:
                        self.delegate.received_queue.get_nowait()
                    except Exception:
                        break
            except Exception as e:
                self.close()
                if isinstance(e, BmapConnectionError):
                    raise
                raise BmapConnectionError("Failed to connect to %s: %s" % (self.mac, e)) from e

        def close(self):
            """Close the channel."""
            if self.channel:
                try:
                    self.channel.closeChannel()
                except Exception:
                    pass
                self.channel = None
            self.device = None
            self.delegate = None

        def send_recv(self, packet, drain=False):
            """Send a BMAP packet and receive the response using NSRunLoop."""
            if not self.channel:
                raise BmapConnectionError("Not connected")

            # Clear delegate queue before sending
            while not self.delegate.received_queue.empty():
                try:
                    self.delegate.received_queue.get_nowait()
                except Exception:
                    break

            # Send
            # writeSync:length:
            status = self.channel.writeSync_length_(packet, len(packet))
            if status != 0:
                raise BmapConnectionError("Write failed with status %d" % status)

            # Brief delay for device to process (similar to original transport)
            time.sleep(0.2)

            # Wait and receive
            data = b""
            start_time = time.time()
            run_loop = NSRunLoop.currentRunLoop()

            # Wait for first response
            while time.time() - start_time < self.timeout:
                run_loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.01))

                if not self.delegate.received_queue.empty():
                    data += self.delegate.received_queue.get()
                    break

                if self.delegate.closed_event.is_set():
                    raise BmapConnectionError("Connection closed by peer")

            if not data:
                raise BmapTimeoutError("No response from device")

            # Drain if requested
            if drain:
                drain_timeout = 0.5
                start_drain = time.time()
                while time.time() - start_drain < drain_timeout:
                    run_loop.runMode_beforeDate_(NSDefaultRunLoopMode, NSDate.dateWithTimeIntervalSinceNow_(0.01))
                    if not self.delegate.received_queue.empty():
                        data += self.delegate.received_queue.get()
                        start_drain = time.time()  # reset drain timeout
                    if self.delegate.closed_event.is_set():
                        break

            return data

        def __enter__(self):
            self.connect()
            return self

        def __exit__(self, *exc):
            self.close()

elif sys.platform == "win32":
    import ctypes
    import select
    import socket
    import struct

    # Winsock constants for Bluetooth sockets (ws2bth.h). Python's socket
    # module has no AF_BTH, but accepts raw ints for family/proto at socket
    # creation. It cannot connect() one, though: getsockaddrarg() rejects
    # byte addresses for unknown families ("connect(): wrong format"), so
    # the raw SOCKADDR_BTH is handed to ws2_32.connect via ctypes and the
    # resulting fd is driven with ordinary Python socket calls afterwards.
    AF_BTH = 32
    BTHPROTO_RFCOMM = 3

    # SOCKADDR_BTH is byte-packed (ws2bth.h wraps it in pragma pack(1)):
    # USHORT family, BTH_ADDR btAddr, GUID serviceClassId (zeros when port
    # is given), ULONG port — 30 bytes total, btAddr at offset 2.
    _SOCKADDR_BTH_FMT = "<HQ16sI"
    _SOCKADDR_BTH_LEN = 30

    # Winsock error codes surface as OSError.errno from socket calls.
    _WSAETIMEDOUT = 10060
    _WSAEWOULDBLOCK = 10035

    _ws2 = ctypes.WinDLL("ws2_32")
    _ws2.connect.argtypes = [ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    _ws2.connect.restype = ctypes.c_int
    _ws2.WSAGetLastError.argtypes = []
    _ws2.WSAGetLastError.restype = ctypes.c_int

    _WSA_NAMES = {
        10013: "permission denied",
        10050: "network down (Bluetooth off?)",
        10060: "timed out",
        10061: "connection refused",
        10064: "host down",
        10065: "host unreachable",
    }

    def _wsa_error(mac, err):
        return BmapConnectionError(
            "Failed to connect to %s: %s" % (
                mac, _WSA_NAMES.get(err, "Winsock error %d" % err)),
            errno=err,
        )

    def _mac_to_bth_addr(mac):
        """Parse a "AA:BB:CC:DD:EE:FF" MAC into a BTH_ADDR integer.

        The address octets map big-endian onto the 48-bit BTH_ADDR
        (NAP = high 16 bits, SAP = low 32), e.g. 00:1A:7D:DA:71:13 ->
        0x001A7DDA7113. Verified against the real device: the reversed
        reading connects to nothing (baseband pages go unanswered).
        """
        try:
            return int(mac.replace(":", "").replace("-", ""), 16)
        except ValueError:
            raise ValueError("Invalid MAC address: %s" % mac)

    class WindowsRfcommTransport:
        """Raw RFCOMM Winsock (AF_BTH) transport for Windows.

        Mirrors LinuxRfcommTransport: same send/delay/recv/drain timing and
        the same errno-carrying error contract for the connect retry logic.
        The device must be paired with Windows; the stack rejects RFCOMM
        connects to unpaired devices.
        """

        def __init__(self, mac, channel=RFCOMM_CHANNEL, timeout=3.0):
            self.mac = mac
            self.channel = channel
            self.timeout = timeout
            self._sock = None

        @classmethod
        def _pack_sockaddr(cls, mac, channel):
            addr = struct.pack(
                _SOCKADDR_BTH_FMT,
                AF_BTH,
                _mac_to_bth_addr(mac),
                b"\x00" * 16,
                channel,
            )
            # sockaddr must fit sa_family + 14 bytes for the generic
            # sockaddr cast; 36 bytes is what SOCKADDR_BTH defines.
            return addr

        def connect(self):
            """Open the RFCOMM socket to the device.

            connect() goes through ws2_32 directly (see the comment at the
            top of this platform block) with a select()-bounded timeout —
            Winsock's own blocking connect can hang for ~20s on an
            unreachable device, which the channel probe cannot afford.
            """
            try:
                sockaddr = self._pack_sockaddr(self.mac, self.channel)
            except ValueError as e:
                raise BmapConnectionError(
                    "Failed to connect to %s: %s" % (self.mac, e)
                ) from e

            sock = None
            try:
                sock = socket.socket(AF_BTH, socket.SOCK_STREAM, BTHPROTO_RFCOMM)
                sock.setblocking(False)
                rc = _ws2.connect(
                    sock.fileno(),
                    ctypes.create_string_buffer(sockaddr, _SOCKADDR_BTH_LEN),
                    _SOCKADDR_BTH_LEN,
                )
                if rc != 0:
                    err = _ws2.WSAGetLastError()
                    if err != _WSAEWOULDBLOCK:
                        raise _wsa_error(self.mac, err)
                    # Connect in flight — wait for it to complete or time out.
                    _, writable, failed = select.select(
                        [], [sock], [sock], self.timeout)
                    if not writable and not failed:
                        raise BmapConnectionError(
                            "Failed to connect to %s: timed out" % self.mac,
                            errno=_WSAETIMEDOUT,
                        )
                    err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                    if err:
                        raise _wsa_error(self.mac, err)
                self._sock = sock
                sock = None
                self._sock.settimeout(self.timeout)
            except BmapConnectionError:
                raise
            except OSError as e:
                raise BmapConnectionError(
                    "Failed to connect to %s: %s" % (self.mac, e),
                    errno=getattr(e, "errno", None),
                ) from e
            finally:
                if sock is not None:
                    sock.close()

        def close(self):
            """Close the socket."""
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

        # Upper bound on chunks discarded before a send, so a device that
        # streams notifications cannot stall the request.
        _MAX_STALE_CHUNKS = 64

        def _discard_pending(self):
            """Drop bytes already waiting on the socket before a new request.

            Late replies and unsolicited STATUS notifications would otherwise
            be read as the answer to the next request. Mirrors the queue
            clear in the macOS transport.
            """
            try:
                self._sock.settimeout(0)
                for _ in range(self._MAX_STALE_CHUNKS):
                    if not self._sock.recv(4096):
                        break  # peer closed; the send will report it
            except (BlockingIOError, InterruptedError, socket.timeout):
                pass
            except OSError:
                pass  # a dead socket fails the send with a clear error
            finally:
                self._sock.settimeout(self.timeout)

        def send_recv(self, packet, drain=False):
            if not self._sock:
                raise BmapConnectionError("Not connected")
            self._discard_pending()
            try:
                self._sock.send(packet)
                time.sleep(0.2)
                data = self._sock.recv(4096)
            except socket.timeout:
                raise BmapTimeoutError("No response from device")
            except OSError as e:
                raise BmapConnectionError("Communication error: %s" % e) from e

            if drain:
                self._sock.settimeout(0.5)
                try:
                    while True:
                        more = self._sock.recv(4096)
                        if not more:
                            break
                        data += more
                except (socket.timeout, BlockingIOError):
                    pass
                self._sock.settimeout(self.timeout)

            return data

        def __enter__(self):
            self.connect()
            return self

        def __exit__(self, *exc):
            self.close()


else:
    import socket

    class LinuxRfcommTransport:
        """Raw RFCOMM Bluetooth socket transport for Linux."""

        def __init__(self, mac, channel=RFCOMM_CHANNEL, timeout=3.0):
            self.mac = mac
            self.channel = channel
            self.timeout = timeout
            self._sock = None

        def connect(self):
            """Open the RFCOMM socket to the device."""
            try:
                self._sock = socket.socket(
                    socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM
                )
                self._sock.settimeout(self.timeout)
                self._sock.connect((self.mac, self.channel))
            except (OSError, socket.error) as e:
                self._sock = None
                raise BmapConnectionError(
                    "Failed to connect to %s: %s" % (self.mac, e),
                    errno=getattr(e, "errno", None),
                ) from e

        def close(self):
            """Close the socket."""
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

        # Upper bound on chunks discarded before a send, so a device that
        # streams notifications cannot stall the request.
        _MAX_STALE_CHUNKS = 64

        def _discard_pending(self):
            """Drop bytes already waiting on the socket before a new request.

            Late replies and unsolicited STATUS notifications would otherwise
            be read as the answer to the next request. Mirrors the queue
            clear in the macOS transport.
            """
            try:
                self._sock.settimeout(0)
                for _ in range(self._MAX_STALE_CHUNKS):
                    if not self._sock.recv(4096):
                        break  # peer closed; the send will report it
            except (BlockingIOError, InterruptedError, socket.timeout):
                pass
            except OSError:
                pass  # a dead socket fails the send with a clear error
            finally:
                self._sock.settimeout(self.timeout)

        def send_recv(self, packet, drain=False):
            if not self._sock:
                raise BmapConnectionError("Not connected")
            self._discard_pending()
            try:
                self._sock.send(packet)
                time.sleep(0.2)
                data = self._sock.recv(4096)
            except socket.timeout:
                raise BmapTimeoutError("No response from device")
            except OSError as e:
                raise BmapConnectionError("Communication error: %s" % e) from e

            if drain:
                self._sock.settimeout(0.5)
                try:
                    while True:
                        more = self._sock.recv(4096)
                        if not more:
                            break
                        data += more
                except (socket.timeout, BlockingIOError):
                    pass
                self._sock.settimeout(self.timeout)

            return data

        def __enter__(self):
            self.connect()
            return self

        def __exit__(self, *exc):
            self.close()


# Dynamically map RfcommTransport based on OS
if sys.platform == "darwin":
    RfcommTransport = MacOsRfcommTransport
elif sys.platform == "win32":
    RfcommTransport = WindowsRfcommTransport
else:
    RfcommTransport = LinuxRfcommTransport
