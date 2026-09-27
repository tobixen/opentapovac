"""The shims that let the vendored TPAP transport run on python-kasa 0.10.2."""

import pytest
from kasa.deviceconfig import DeviceConfig
from kasa.exceptions import _RetryableError

from opentapovac import _tpap as tp


async def test_stat_access_error_is_retryable_and_printable():
    session = tp.TpapTransport(config=DeviceConfig("tpap-host"))._encryption_session
    with pytest.raises(_RetryableError) as err:
        session._handle_response_error_code({"error_code": -2203}, "request")
    # 0.10.2's DeviceError.__str__ reads error_code.name
    assert "STAT_ACCESS_ERROR(-2203)" in str(err.value)
    assert tp.TpapTransport._should_retry_live_session(err.value)


class FakeTransport:
    """Answers -2203 first, then the real reply."""

    _host = "tpap-host"
    _config = DeviceConfig("tpap-host")

    def __init__(self):
        self.replies = [{"error_code": -2203}, {"error_code": 0, "result": {"battery_percentage": 87}}]
        self.resets = 0

    async def send(self, request):
        return self.replies.pop(0)

    async def reset(self):
        self.resets += 1

    async def close(self):
        pass


async def test_protocol_retries_stat_access_error():
    transport = FakeTransport()
    protocol = tp.TpapSmartProtocol(transport=transport)
    reply = await protocol.query({"getBatteryInfo": None})
    assert reply == {"getBatteryInfo": {"battery_percentage": 87}}
    assert transport.resets == 1
