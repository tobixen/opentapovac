"""Robot status and error codes in words (app `RobotStatusType`, `RobotCleanErrorType`)."""

STATUS = {
    0: "standby",
    1: "cleaning",
    2: "mapping",
    3: "remote control",
    4: "going home",
    5: "charging",
    6: "charged",
    7: "paused",
    8: "sleeping",
    9: "emptying dust",
    11: "going to a point",
    15: "washing mop",
    16: "drying mop",
    17: "fitting mop",
    18: "removing mop",
    19: "cutting hair",
}

#: only the ones seen so far; the rest are in `RobotCleanErrorType.java`
ERRORS = {
    3: "stuck",
    4: "lifted (wheels off the floor)",
    21: "dock not found",
    26: "clean water tank empty or missing",
}


def status_text(code: int | None) -> str:
    if code is None:
        return "unknown"
    return STATUS.get(code, f"status {code}")


def error_text(code: int) -> str:
    return ERRORS.get(code, f"error {code}")
