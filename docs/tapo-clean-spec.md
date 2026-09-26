# `tapo-clean` — room-cleaning CLI for the Tapo RV50 (spec, not built)

Written 2026-09-24 for whoever builds it.  Nothing here exists yet.
Read first: `field-notes.md` (robot, map, room ids, how to
connect), `protocol.md` section "Cleaning" (payloads, status and
error codes), and `tapo-run-queue.sh` (the shell prototype this replaces).

## Goal

Clean one room or several from the command line, the way the app lets
you pick several rooms for one run.  Bash tab completion on room names.

```
tapo-clean kitchen "outer hall"              # default: vacuum then mop
tapo-clean --vac hall   |  --mop hall  | --vac-and-mop hall | --vac-then-mop hall     # clean_type
tapo-clean --suction 2 --water 2 --passes 1 hall
tapo-clean --sequential kitchen hall         # one run per room, queued on the laptop
tapo-clean --wait kitchen                    # follow until docked, report errors
tapo-clean --status | --battery | --stop | --rooms | --refresh-rooms
```

Exact flag names are open.  Keep the flag → payload mapping in one table;
don't scatter it through the code.

## Feedback and logging

Should the CLI just send commands to the robot and then return, or should the commands be blocking and receiving feedback all until the operation is done?

Possibly the communication should go via a daemon to allow desktop notifications to be sent and logs to be received?  (to make it easier for potential other users to start using this, perhaps the CLI should work in a stand-alone-mode if the daemon is not running)

Note: this section was added by the human, after the rest of the document was written.  I believe the original idea is "blocking and reporting".  The rest of the document may need a rethink if a daemon is to be added.  I see there is some text about the monitoring-loop.

## Goal, rethinking

I will have to let my family use this, and they may not want to use the cli.  Is it possible to install python on the openwrt router?  I'm thinking to set up some simple web-ui.  It should not try to replicate all the functionality in the app.  I can give the other family members access to the app.  What is needed is a dead-simple web-interface for daily operations needed in our home + the specifics for our home:
* The living room and bedroom 2 is special, the human needs to carry the bot to the room, and the system needed to verify that it can find the correct position at the map after the carry-operation.
* Ordering matters - if more than one room is to be cleaned, then the outer hall and the bedroom 1 should always be cleaned first (to avoid the robot becoming stuck when returning to base)
* When the robot can't find the base, it's needed with some logic:
  * The robot is not in the kitchen?  Then try to navigate it first to the hall and then to the kitchen, and then dock.  If the washing program got interrupted, then try to figure out what is done and what is not done and queue up the remaining tasks.
  * The robot is in the kitchen?  Human needs to check that the base is connected to electricity (the same socket is used for kitchen utensils - we need to get some more sockets installed) and that there are no chairs in the way.
* Simple web interface.  Buttons for each room, plus a button for halls + kitchen + bedroom 1.  Also easy to choose between vac only, vac+mop, vac then mop and mop only.  Button for "abort / return to base".  Status box with logs / messages (with timestamps in Oslo-time).  Map - with a reload-button to regenerate the map.  The map in the app is neat, it shows where the bot has been and also where it has cleaned.

The project should be open-sourced - meaning that the "specifics for our home" mostly should land in a config file - but it's also possible to split it into an open-source general python package and make the web-system and local logic in a separate package.  Though, "we have some rooms where it's needed to carry the robot" and "robot got stuck while trying to return to base" are most likely not unique things with our home.

The project can run either externally, on one of my servers (an Ubuntu box managed by puppet, or a NixOS box).  There is a complication since the OpenWRT router even has dynamic IPv6 - from time to time the ISP decides to rotate it.  We'd probably also need some kind of authentication.  Wireguard was already on the table.  Ideally I'd have it run on a local computer, but it does not seem like I will be able to set up something here and now.  Perhaps it can run from a family member's server.  In any case, the development and testing can be done from this laptop.

## Decisions already made

* Python, calling the python-kasa **library** directly.  Don't shell out to
  the `kasa` CLI; that's what `tapo-run-queue.sh` does, and it's why errors
  get lost (see below).  (but if this should run from the OpenWRT router it probably needs to be implemented in C?).
* python-kasa comes from `<python-kasa checkout>`, branch `tpap-rv50-tls-fix`
  (unmerged upstream).  The connection is the one the `kasa` command line
  in `field-notes.md` builds (`--port 4433 --https -e tpap -df
  SMART.TAPOROBOVAC`); copy how `kasa/cli` turns those flags into a
  `DeviceConfig`.  Raw calls go through the device's query/`_query_helper`
  the way `kasa command` does.
* The host is **not hard-coded**.  The robot has moved once already, from
  192.168.1.73 to 10.47.128.10.  Read it from a config
  file under `~/.config/tapo/` (next to `credentials.yaml`), with a
  `--host` override.
* Room names come from the robot, not from a table in the code.  The ids
  have changed once already (the merge/split on 2026-09-24).

## Open decisions (ask the user)

* Where it lives: the user's personal scripts repo or
  this repo next to `tapo-render-map.py`.
* Whether `tapo-run-queue.sh` is deleted once `--sequential` works (the
  lean is yes, to avoid two queue implementations).
* Room 6 has **no name** on the robot.  The user calls it "the outer hall".
  Name it in the app or with `setAreaInfo` (see `protocol.md`,
  "Editing the map") before relying on names.  Until then, completion
  should also accept bare ids.
* Upstream: a room-clean command in python-kasa itself might be the better
  home long term.  That's someone else's project, so it would be an
  upstream contribution.  Not for the first version.

## Rooms and completion

* Source: `getMapData` → `area_list`, entries with `type == "room"`; `name`
  is base64 (UTF-8).  Current names: 1 `kjøkken`, 2 `living room`,
  3 `stairs`, 4 `bedroom 1`, 5 `hall`, 6 *(none)*, 7 `bedroom 2`.
* `map_id` is also in `getMapData` (1789832484 now); send it along if the
  payload needs it, don't hard-code it.
* Cache the id↔name list in a file (`~/.cache/tapo/rooms.json` or similar)
  so completion never talks to the robot.  `--refresh-rooms` rewrites it.
  Consider refreshing it after every successful run as well.
* Names have spaces and `ø`.  Completion has to quote them correctly.
  Aliases would help (`kitchen` → `kjøkken`, `outer hall` → 6): keep them in
  the config file.  Matching is case-insensitive.
* Refuse room 3 (stairs, no-go zone 301) unless `--force`.
* Completion: `argcomplete` if a dependency is acceptable, otherwise a small
  hand-written bash completion that reads the cache file.  Offer room names
  that are already on the command line only once.

## Payloads

The known-good payload (vacuum then mop, one room) is in protocol.md and
`payloads/runCleanTask-*-vac-then-mop.json`.  A run is
`runCleanTask` with `clean_mode 3`, `is_custom true`, `clean_order true`, and
one `area_list` item per room carrying its own `clean_type`, `suction`,
`cistern`, `clean_number`, `density`.  Pitfalls, all hit already:

* `kasa command` parses params with `ast.literal_eval`.  Not a problem when
  using the library, but don't copy the JSON → `repr` hack.
* A new `runCleanTask` while a run is going fails with **-3002**.  kasa
  only logs a warning.  The library path must turn that into an error.
* After a run the robot passes through status 5 only briefly (4 → 5 → 19
  → 9 → 17 → 15 → 16, about 5 minutes).  A send during that 5 got an empty
  reply and was dropped.  "Idle" means 16, or 5/6 seen twice in a row
  about 20 s apart.
* `--stop` is `runCleanTask {"clean_on": false, …}` (protocol.md).

## `--wait` / `--sequential`: the monitor loop

Poll `getVacStatus` every ~20 s and print changes, decoded to words
(status codes and `err_status` codes are in protocol.md and
`RobotCleanErrorType.java` in the decompiled app).  It must handle what
`tapo-run-queue.sh` doesn't:

* **Check the send.**  Treat anything but a `null` result as failure.  Then
  confirm the run started: status 1 (or 17/15, mop prep) within ~2 min.
  Otherwise report it and stop; never assume it went.
* **Terminal states:** 16, or 5/6 seen twice, after the robot has left the
  dock (status 1 or 4 seen) = done.  Status 0 with an error = gave up.
  Report it and exit non-zero; don't loop for an hour.
* **Stuck:** status 7 with err 3 (`ROBOT_ERR_MIDDLE_STUCK`) = needs a
  human.  Report it loudly (a desktop notification if available), keep
  watching, and carry on when the robot resumes.
* **err 21** (`CHARGE_STATION_IS_NOT_FOUND`), status 0: stranded on the way
  home.  Report it.  Sending the robot home is `setSwitchCharge`, never
  sent yet, so test that first (see below).
* **err 26**: clean water tank empty.  The robot carries on; warn.
* The next queued room goes out only once idle as defined above.
* Before sending, check `getBatteryInfo` and `getBaseStatus`
  (`clean_water` 1 = empty) and warn.
* Log to a file as well as the terminal.  The laptop has to stay on the
  robot's network while the queue runs; say so in `--help`.

## Doorsteps: what we know (read before designing retries)

Seen by the user, over several runs:

* **While cleaning** (including moving between rooms in one multi-room
  run), the robot usually gets over the lower doorsteps by itself.  Sometimes
  it takes a while, and a gentle kick speeds it up.  On 2026-09-24 21:09 it
  paused with err 3 at the kitchen/hall doorstep and went on after a kick.
* **When going back to the dock** it gives up at a doorstep instead of
  trying: 2026-09-24 21:49, coming home from room 6 after the mop pass, it
  stopped with err 21, dock not found.  At 21:39 the same route home, after
  the vacuum pass, worked.
* Known problem 4 in `field-notes.md`: two doorsteps can only be
  crossed one way.  Ramps maybe.

Guesses, none tested: the homing planner may treat a doorstep as a wall
rather than something to climb; the mop being fitted on the way home after
the mop pass may make climbing harder; err 21 may just be how "path to dock
blocked" is reported.  Consequences for the CLI:

* Multi-room runs are probably **better** than one run per room: fewer
  trips home, so fewer homing crossings.  Mop-only runs through a doorstep
  are the risky case.
* When err 21 comes, a "go home" retry (`setSwitchCharge`) may be enough,
  as tonight's recovery a minute later suggests.  One retry at most, then
  ask a human.  Test it before building it in.
* **Going home from behind an impassable doorstep: pause, don't let it
  fail.**  If the robot signals that it needs the dock mid-run (mop wash,
  water, battery, end of run: `getCleanStatus.recharge_status` 1, or
  status 4) while it is in a room behind a doorstep it can't cross that
  way, pause the run and ask a human to carry it over; resume once it's
  put down on the dock side.  Otherwise it stops at the doorstep with
  err 21 and the whole run is dropped.  No need to worry about wet mops
  on the floor while paused: the robot lifts and lowers the mop as
  needed.  Needs a per-room note of which doorsteps are one-way (bedroom 2:
  out only, carried in; living room: in only, carried out).
* If it's possible to specify the order of cleaning, it's just to make sure the kitchen (where the base is located) is the last room to be cleaned.
* Perhaps it's possible to solve it physically by adding some ramps by the doorsteps.

## Relocation ("finding position"): watch it, don't trust it

After it is lifted, untangled or stuck, the robot re-finds its position.
If it concludes it is somewhere wrong and the map isn't locked, it
clobbers the map (known problem 3).  So the monitor loop must watch
relocation, not only `getVacStatus`:

* **Signal:** `getCleanStatus` → `is_relocating` (and `is_mapping`: a
  cleaning run that starts mapping is building a new map — treat as
  alarm).  Also `clean_status`, `recharge_status`.  `getVacStatus`
  err 4 = lifted (wheels off the floor); it resumes by itself ~20 s
  after being put down.
* **Expected moves:** being carried into the room it was told to clean,
  and being lifted over a doorstep by a human.  Any other big jump in
  position is unexpected.  With nobody home the robot can't have moved
  far by itself, so a relocation result far from the last known
  position (`real_vac_coor` / `getPathData`) is almost certainly wrong.
* **On relocation:** pause the robot (`setRobotPause`) and ask for a
  human (desktop notification, and the robot's own voice if there is a
  method for it).  If a human answers, let them confirm or fix the
  position.  If nobody answers within a few minutes: move the robot a
  little — preferring a turn on the spot, since with a wrong position
  the no-go zones (stairs, 301) are in the wrong place and only the
  cliff sensors protect — and restart relocation.  Give up after a
  couple of tries and leave it paused.
* **Lock the map.**  The house is mapped; the map is locked with
  `setMapInfo {"map_list": [{"map_id": 1789832484, "map_locked": True}]}`
  (app `RobotMapRepository.H7`; `getMapInfo.map_list[].map_locked`
  reads it back, 0/1).  Probably only allowed while docked.  The CLI
  should refuse to start a run on an unlocked map, or warn loudly.
  `auto_change_map` is off (2026-09-25); the CLI should check it too.
* Unverified: what the robot does when relocation fails on a locked map
  (error? stop?).  Find out before relying on it.

## Tests to do on the robot before (or while) building

Each with someone at home watching, one at a time, reading state back
afterwards.  Log results in `protocol.md` and the diary in
`field-notes.md`.

1. **Multi-room run, two adjacent rooms without a doorstep between them**
   (e.g. hall 5 + room 6), vacuum only.  Does the robot accept a
   two-item `area_list`?  Does it clean in list order (swap the order in a
   second run)?  Partly answered: bedroom 1 + kitchen in one run worked
   on 2026-09-25 (see `field-notes.md`); list order not checked.
2. **Different `clean_type` per room in one run** (e.g. hall vacuum only,
   room 6 vacuum then mop).  Is each room's own setting honoured?  In what
   order are the vacuum and mop passes done: per room, or all vacuum
   first?
3. **Multi-room run across the kitchen doorstep**, vacuum only.  Compare
   with the observations above.
4. **`setSwitchCharge`** (send home) while the robot is out.  First read the
   payload from the app sources.  Then try it on purpose after an err 21,
   if one comes up.
5. **`--stop` mid-run**, then a new run straight after: how long until
   -3002 stops?
6. **Pause/resume** (`setRobotPause`): not needed for v1, but it would let
   the tool resume after a stuck without the app.
7. **What status sequence a send during 19/9/17/15 gives.**  Does it
   always fail, and does it fail the same way every time (empty reply or
   -3002)?  This decides how strict "idle" has to be.

Software tests (pytest, no robot needed): name/alias → id resolution, the
flag → payload mapping (compare with the known-good JSON in
`payloads/`), and the monitor as a state machine, fed with
recorded status sequences.  The 2026-09-24 evening run (kitchen, rejected
send, room 6, stuck, err 21) is in
`status-2026-09-24-evening.log` (not in this repository yet; goes to `tests/fixtures/`); use it as the first
fixture.  Write these before the code, per the user's
usual rule.

## Afterwards

* Update `field-notes.md` ("Tools") and protocol.md.
* Delete or replace `tapo-run-queue.sh` (see open decisions).
