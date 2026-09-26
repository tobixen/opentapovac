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
