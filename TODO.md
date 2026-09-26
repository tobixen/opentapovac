# TODO

## Monitor and engine, from the first real run (2026-09-26, field-notes.md)

* **Lifted into standby is not "gave up".**  Carried after the run, the
  robot sat at status 0 with err 4 for 5 minutes and the job was failed
  as "robot gave up".  Err 4 needs a human (button, see protocol.md), so
  it should ask one, not time out.  Done for carry rooms (no giving up
  while a carry question is open); still open for other runs.
* **The idle check refuses standby with err 4**, so a new run can't be
  sent after a carry until the error is cleared by the button.  Consider a
  `--no-idle-wait` option for when a human is next to the robot.
* **`pause`/`resume` commands** (`setRobotPause`): accepted, but no effect
  from standby with err 4; still untested on a paused run (status 7),
  which is what the carry flow needs.
* **A run abandoned after err 21 is only flagged** ("maybe unfinished",
  2026-09-26 evening).  Which rooms were done is unknown; design.md §4.3
  wants "queue the rest".  The recorded track and the clean record are
  the evidence to go on.
