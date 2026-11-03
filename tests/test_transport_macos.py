import sys
import pytest
from pybmap.errors import BmapConnectionError
from pybmap.transport import RfcommTransport

@pytest.mark.skipif(sys.platform != "darwin", reason="Only runs on macOS")
def test_macos_transport_invalid_mac():
    with pytest.raises(BmapConnectionError) as exc_info:
        transport = RfcommTransport("00:11:22:33:44:55")
        transport.connect()
    # Check that a connection error containing a failure explanation was raised
    error_msg = str(exc_info.value)
    assert any(word in error_msg for word in ["Failed", "Device not found", "connection"])
