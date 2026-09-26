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

## From the code review (2026-09-26)

* **`stop()` may cancel a query mid-flight**, then send STOP at once; if
  the TPAP session is left out of step, STOP fails (502) and the job says
  "stopped" while the robot runs on.  Never seen; test on the robot, then
  close the session on `CancelledError` in `KasaRobot._raw` or retry STOP.
* **Cleared-track detection** only notices `total_points < start`; a track
  regrown past that between polls is glued onto the old segment, minus its
  start.  Decide with a few server-recorded runs: compare the first point,
  or refetch from 0 after an error.
* **API error handling:** `?backlog=abc` gives a text 500, and the CLI then
  shows a traceback (`ContentTypeError`).  Validate, add a JSON catch-all to
  the middleware, read non-JSON error bodies as text.
* **CLI on the NixOS host:** the state dir is 0700 under `DynamicUser`, so
  `opentapovac log` (without `-f`) shows nothing and the standalone
  fallback tracebacks on the credentials.  Document, or give the service a
  fixed group an admin can join.
