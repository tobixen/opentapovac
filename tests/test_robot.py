import logging

import pytest

from opentapovac.robot import KasaRobot, RobotError


async def test_unexpected_library_error_becomes_robot_error(caplog):
    robot = KasaRobot("192.0.2.1", "u", "p")

    async def broken_connect():
        raise TypeError("'mpz' object is not an instance of 'int'")

    robot._connect = broken_connect
    with caplog.at_level(logging.DEBUG, logger="opentapovac.robot"), pytest.raises(RobotError, match="mpz"):
        await robot.query("getVacStatus")
    assert "Traceback" in caplog.text  # the details stay available with -v


async def test_connect_builds_a_tpap_protocol_without_device_detection():
    # the vendored transport (opentapovac._tpap), so a released python-kasa
    # without TPAP will do: no DeviceEncryptionType.Tpap, no Device.connect
    from opentapovac._tpap import TpapSmartProtocol, TpapTransport

    robot = KasaRobot("192.0.2.1", "u", "p", port=4433, timeout=7)
    protocol = await robot._connect()
    try:
        assert isinstance(protocol, TpapSmartProtocol)
        transport = protocol._transport
        assert isinstance(transport, TpapTransport)
        assert str(transport._bootstrap_url) == "https://192.0.2.1:4433"
        assert transport._config.timeout == 7
        assert transport._credentials.username == "u"
        assert transport._config.connection_type.device_family.value == "SMART.TAPOROBOVAC"
    finally:
        await protocol.close()
