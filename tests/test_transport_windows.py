"""Tests for the Windows RFCOMM Winsock (AF_BTH) transport.

These tests run on Windows only and mock the socket layer — no Bluetooth
hardware required. They pin down the SOCKADDR_BTH wire format (byte-packed,
btAddr big-endian at offset 2) that a mis-layout silently corrupts into
WSAEADDRNOTAVAIL.
"""

import socket
import struct
import sys
import pytest
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Only runs on Windows")

from pybmap.errors import BmapConnectionError, BmapTimeoutError
from pybmap import transport as transport_mod
from pybmap.transport import RfcommTransport


@pytest.fixture(autouse=True)
def _no_stale_discard(request, monkeypatch):
    """Keep the pre-send discard out of tests that script recv() calls.

    TestWindowsDiscardPending exercises it directly.
    """
    if request.cls is not None and request.cls.__name__ == "TestWindowsDiscardPending":
        return
    monkeypatch.setattr(RfcommTransport, "_discard_pending", lambda self: None)


class TestSockaddrFormat:
    """SOCKADDR_BTH packing — validated against the real device."""

    def test_layout_is_packed_30_bytes(self):
        sa = RfcommTransport._pack_sockaddr("00:1A:7D:DA:71:13", 2)
        assert len(sa) == 30
        family, = struct.unpack_from("<H", sa, 0)
        btaddr, = struct.unpack_from("<Q", sa, 2)
        port, = struct.unpack_from("<I", sa, 26)
        assert family == 32
        assert btaddr == 0x001A7DDA7113  # big-endian octets onto 48 bits
        assert port == 2

    def test_btaddr_big_endian(self):
        sa = RfcommTransport._pack_sockaddr("E4:58:BC:C1:39:84", 8)
        btaddr, = struct.unpack_from("<Q", sa, 2)
        assert btaddr == 0xE458BCC13984

    def test_invalid_mac_rejected_upfront(self):
        with pytest.raises(BmapConnectionError) as exc:
            RfcommTransport("not-a-mac").connect()
        assert "not-a-mac" in str(exc.value)


class TestConnect:
    @patch("select.select")
    @patch.object(transport_mod, "_ws2")
    @patch("socket.socket")
    def test_connect_success(self, mock_socket_class, mock_ws2, mock_select):
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        mock_ws2.connect.return_value = 0

        t = RfcommTransport("E4:58:BC:C1:39:84")
        t.connect()

        mock_socket_class.assert_called_once_with(32, socket.SOCK_STREAM, 3)
        # Raw sockaddr handed to ws2_32.connect: 30 bytes, little-endian
        # buffer, correct length.
        args = mock_ws2.connect.call_args[0]
        raw = bytes(args[1].raw)
        assert len(raw) == 30
        assert args[2] == 30
        assert struct.unpack_from("<H", raw, 0)[0] == 32
        mock_sock.settimeout.assert_called_with(3.0)

    @patch("select.select")
    @patch.object(transport_mod, "_ws2")
    @patch("socket.socket")
    def test_connect_refused_carries_errno(self, mock_socket_class, mock_ws2, mock_select):
        """The channel probe's retry logic keys off this errno."""
        mock_socket_class.return_value = MagicMock()
        mock_ws2.connect.return_value = -1
        mock_ws2.WSAGetLastError.return_value = 10061  # WSAECONNREFUSED

        t = RfcommTransport("E4:58:BC:C1:39:84")
        with pytest.raises(BmapConnectionError) as exc:
            t.connect()
        assert exc.value.errno == 10061
        assert "refused" in str(exc.value)

    @patch("select.select", return_value=([], [], []))
    @patch.object(transport_mod, "_ws2")
    @patch("socket.socket")
    def test_connect_timeout(self, mock_socket_class, mock_ws2, mock_select):
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        mock_ws2.connect.return_value = -1
        mock_ws2.WSAGetLastError.return_value = 10035  # WSAEWOULDBLOCK

        t = RfcommTransport("E4:58:BC:C1:39:84")
        with pytest.raises(BmapConnectionError) as exc:
            t.connect()
        assert exc.value.errno == 10060  # WSAETIMEDOUT — the busy analogue

    @patch("select.select")
    @patch.object(transport_mod, "_ws2")
    @patch("socket.socket")
    def test_connect_async_completion(self, mock_socket_class, mock_ws2, mock_select):
        """A WSAEWOULDBLOCK connect completes via select + SO_ERROR=0."""
        mock_sock = MagicMock()
        mock_socket_class.return_value = mock_sock
        mock_ws2.connect.return_value = -1
        mock_ws2.WSAGetLastError.return_value = 10035
        mock_select.return_value = ([], [mock_sock], [])
        mock_sock.getsockopt.return_value = 0

        t = RfcommTransport("E4:58:BC:C1:39:84")
        t.connect()
        mock_sock.settimeout.assert_called_with(3.0)


class TestSendRecv:
    @patch("time.sleep")
    def test_send_recv_roundtrip(self, mock_sleep):
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b"\x1f\x01\x06\x02\x80\x64"

        t = RfcommTransport("E4:58:BC:C1:39:84")
        t._sock = mock_sock  # skip connect(); under test here is the I/O path
        reply = t.send_recv(b"\x1f\x01\x02\x02\x05\x00")
        assert reply == b"\x1f\x01\x06\x02\x80\x64"
        mock_sock.send.assert_called_once_with(b"\x1f\x01\x02\x02\x05\x00")

    @patch("time.sleep")
    def test_send_recv_timeout(self, mock_sleep):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = socket.timeout("timed out")
        t = RfcommTransport("E4:58:BC:C1:39:84")
        t._sock = mock_sock
        with pytest.raises(BmapTimeoutError):
            t.send_recv(b"\x02\x02\x01\x00")

    def test_not_connected(self):
        t = RfcommTransport("E4:58:BC:C1:39:84")
        with pytest.raises(BmapConnectionError):
            t.send_recv(b"\x02\x02\x01\x00")

    @patch("time.sleep")
    def test_drain_collects_until_timeout(self, mock_sleep):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [
            b"\x1f\x01\x06\x02\x80\x64",
            b"\x1f\x03\x06\x03\x01\x02\x03",
            socket.timeout("drain"),
        ]

        t = RfcommTransport("E4:58:BC:C1:39:84")
        t._sock = mock_sock
        result = t.send_recv(b"\x1f\x01\x02\x02\x05\x00", drain=True)
        assert result == b"\x1f\x01\x06\x02\x80\x64\x1f\x03\x06\x03\x01\x02\x03"
        # No initial 3.0 here: connect() owns that call; the drain cycle is
        # [enter 0.5, restore 3.0].
        timeouts = [c[0][0] for c in mock_sock.settimeout.call_args_list]
        assert timeouts == [0.5, 3.0]


class TestWindowsDiscardPending:
    def test_stale_bytes_dropped(self):
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = [b"\x1f\x03\x03\x01\x00", BlockingIOError()]

        t = RfcommTransport("E4:58:BC:C1:39:84")
        t._sock = mock_sock
        t._discard_pending()
        # Non-blocking for the discard, restored afterwards (connect() set
        # the initial 3.0, which this test skips).
        timeouts = [c[0][0] for c in mock_sock.settimeout.call_args_list]
        assert timeouts == [0, 3.0]

    def test_discard_is_bounded(self):
        mock_sock = MagicMock()
        mock_sock.recv.return_value = b"\x1f\x03\x03\x01\x00"

        t = RfcommTransport("E4:58:BC:C1:39:84")
        t._sock = mock_sock
        t._discard_pending()
        assert mock_sock.recv.call_count == RfcommTransport._MAX_STALE_CHUNKS
