# TODO

## From 2026-09-28 (field-notes.md)

* **Robot time and quiet hours:** `time_diff` 60 on a UTC+2 day, and
  do-not-disturb 22:00–08:00 robot time (23:00–09:00): likely why the
  bin isn't emptied and the voice is quiet on morning runs.  Fix the
  offset (`setDeviceTime`) or the hours (`setDoNotDisturb`), in the app
  or from here; show both in `opentapovac status`, and warn when a run
  starts inside the quiet hours.
* **Dry mop**, even at water 3: a mop run after 09:00 tells DND from
  hardware (pump, filter, pads; `rag_time` reads 0).
* **The app shows the robot offline** since the egress was closed, and
  still with it open again.  Find out what brings it back (robot
  restart? re-pair?), and whether the app ever talks to it locally.
* **Move the living room / hall border** 10 cm into the hall:
  `docs/scripts/move-border-living-room-hall.py MAP-BACKUP --send`, with
  the robot idle; then refresh the rooms on broxbox06 and check that the
  preset's ids (5 = hall) still hold.
* **Carry-out rooms re-entered after a mid-run mop wash** (the living
  room at 56 %): each trip home is another carry.  A "room done, go on"
  answer on the carry-out question (as carry-in's "skip").
* **Not yet tried on the robot:** pause for a carry and resume after it,
  pausing while it goes home (left the base's room) and what status it
  shows once put down, the job queue (in memory only: a restart loses
  it).  Watch the first real use.
* **The laptop CLI reaches broxbox06 only through an ssh tunnel**:
  `DaemonClient` has no basic auth for the nginx front.
* `dock: true` in the room config is read by nothing.

## From the review of the track fix (2026-10-01)

* `mapimg.drop_lost` drops a sub-path that starts within 100 mm of
  (0, 0), up to its first jump over 1 m.  With a dock at (0, 0) (not
  this house: (4167, 3651)) a run from the dock followed by a carry
  could lose its points up to the carry.  Excluding starts near
  `real_charge_coor` needs the map in `Track.lines`.

## From the pre-release review (2026-09-28)

* **A robot paused for a carry stays paused** if the job ends any other
  way than an answer or being carried (Home, goto, a failed position
  check, an error): `_follow`'s `finally` doesn't resume it, and whether
  `setSwitchCharge` works on a paused robot is untested.  Resume in
  `finally` unless stopped, with a test for Home during a carry question.
* **Pause and queue gaps:** no test for `pause_failed`; a manual
  pause/resume during a carry doesn't update the engine's `paused` flag;
  the old job's tail track (mop wash) isn't recorded against it when a
  queued job starts at once; `Engine.wait()` can raise `CancelledError`
  from `asyncio.shield` if a queued job was cancelled before it started.
  Missing tests: Home/goto dropping the queue, a queued job after "no" to
  a redo question, and `test_position_found_after_a_relocation` pins the
  poll count.

## Monitor and engine, from the first real run (2026-09-26, field-notes.md)

* **Lifted into standby is not "gave up".**  Carried after the run, the
  robot sat at status 0 with err 4 for 5 minutes and the job was failed
  as "robot gave up".  Err 4 needs a human (button, see protocol.md), so
  it should ask one, not time out.  Done for carry rooms (no giving up
  while a carry question is open); still open for other runs.
* **The idle check refuses standby with err 4**, so a new run can't be
  sent after a carry until the error is cleared by the button.  Consider a
  `--no-idle-wait` option for when a human is next to the robot.
* **Pause for a carry, resume after it** is built (design.md) but
  untried: whether `setRobotPause {"pause": false}` resumes a robot that
  was paused, lifted and put down, or err 4 needs the button again.
* **A run abandoned after err 21 is sent again as a whole** (design.md).
  Which rooms were done is unknown; resending only the rest would need
  the recorded track and the clean record as evidence.

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

## Map

* **Is `mop_state` "mopping now" or "mop fitted"?**  False all through
  the vacuum pass of a vac_then_mop run (2026-09-28), but the mop is
  fitted only after it there, so either reading fits.  Still open for
  app runs that vacuum with the mop fitted; the map and the missed-mop
  check both lean on it.
* **`goto ROOM` can pick a spot next to a wall**: the room pixel nearest
  its centroid.  Prefer the one farthest from the room's edge.
* **Draw the tracks in the browser.**  Serve the map without tracks (it
  rarely changes) and `/tracks.json` with the lines and the map geometry
  (origin, resolution, height), and draw them on a `<canvas>` over the
  image.  The checkboxes and max age then filter without a round trip,
  and the event stream can add points live.  Costs the point-type colours
  and the coordinate transform in JS, next to mapimg.py.

## From the 2026-09-26 evening runs (field-notes.md)

* **Planner or position?**  Log `real_vac_coor` with the progress line and
  compare with what a human sees: a wrong position means mislocalization
  (relocation watchdog, design.md §4, gets priority); a right position and
  a wrong route means the firmware planner.
* **Skipped vacuum pass** (22:18): find out why.  Try `support_continue:
  false`, or a stop before the run, and see if it vacuums.  (`getMopState`
  is now read on every poll while cleaning, so a short vacuum pass is no
  longer missed.)
* **The kitchen doorstep** is lost in both directions: check for
  obstacles, or a ramp; a no-go line in front of the living room keeps the
  planner out of it when the living room is not in the run.  The
  `no_progress` warning resets on any status but cleaning, so a robot
  flipping between cleaning and relocating there never trips it: check the
  22:47 log, and count any active status as working if so.
* **Progress line tests:** nothing covers `getCleanInfo`, `getMopState` or
  the battery failing during a probe, or `vacuum_first` passed on from a
  vacuum-then-mop run; `test_progress_logged_every_minute` pins exactly 3
  lines to the fake clock.
* ~~`gotoPoint`~~ works (2026-09-28), and so does the home route.
