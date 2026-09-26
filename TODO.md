# TODO

## Monitor and engine, from the first real run (2026-09-26, field-notes.md)

* **"Going home" at the start counts as having left the dock.**  From
  standby off the dock the robot first goes to the base to fit the mop
  (4 → 17 → 15 → 1).  If it had then charged or dried instead of setting
  off, the monitor would have reported the run as done.  Only count
  status 1 (or 4 after a 1) as having left the dock.
* **Lifted into standby is not "gave up".**  Carried after the run, the
  robot sat at status 0 with err 4 for 5 minutes and the job was failed
  as "robot gave up".  Err 4 needs a human (button, see protocol.md), so
  it should ask one, not time out.
* **The idle check refuses standby with err 4**, so a new run can't be
  sent after a carry until the error is cleared by the button.  Consider a
  `--no-idle-wait` option for when a human is next to the robot.
* **`pause`/`resume` commands** (`setRobotPause`): accepted, but no effect
  from standby with err 4; still untested on a paused run (status 7),
  which is what the carry flow needs.
