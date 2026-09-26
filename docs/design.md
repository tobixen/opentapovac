# OpenTapoVac — design (draft 2, 2026-09-26)

Replaces the old spec `tapo-clean-spec.md`
("tapo-clean"), which is still the reference for payloads, status
codes, pitfalls and the robot tests.  This document only covers what
changes: a daemon, a web UI for the family, and the home-specific
logic.  Milestones 1–4 are built (2026-09-26), see §9; none of it has
cleaned a room yet.

## 0. Arguments against, read first

1. **Home Assistant + TapoVac-ADV already does most of this.**  It is
   verified on the RV50 Pro Omni and gives room buttons, a map card,
   authentication, a phone app with push notifications, and remote access.
   Our special logic (room order, carry-rooms, dock recovery) could be an
   HA script or a small custom integration calling `vacuum.send_command`.
   What HA does *not* give: the relocation watchdog and the carry flow as
   real state machines with tests, and it is a big thing to run 24/7 for one
   robot.  **This is the first decision (Q1).**  The rest of this document
   assumes our own daemon.
2. **Ramps help, but don't remove the special logic.**  Homing failures
   have been seen at every doorstep the robot has crossed, not only the
   two one-way ones (the author's observation; not every run is in the
   field notes).  Logged: err 21 on the way home from the outer hall
   (after the mop pass; the same route worked after the vacuum pass) and
   from bedroom 2.  Separately, err 3 (stuck) at the kitchen doorstep
   while cleaning.  Ramps on the
   one-way doorsteps would drop two carry-rooms, but the carry flow is
   needed anyway: the robot has not been in the bathroom or the attic
   yet, and those will likely need carrying too.  Keep carry-rooms a
   config setting, so a ramp is a config change.
3. **The recovery logic rests on untested calls.**  "Drive to the hall,
   then the kitchen, then dock" needs `gotoPoint` (never sent) or a
   zone-clean stand-in (never tried).  Resuming an interrupted run needs
   to know what was done (`getPathData`, `getCleanRecords` — unclear).
   v1 must work without them: stop, report, ask a human.
4. **Reverse-engineered protocol on an unmerged python-kasa branch.**  A
   firmware update can break everything.  Pin the branch, keep the robot's
   egress closed when not updating, and don't make the family depend on
   it for anything the app can't also do.
5. **Not on the router.**  Python on OpenWrt is possible (`python3` is in
   the package feed), but python-kasa pulls in `aiohttp`, `cryptography`,
   `mashumaro` and friends; not all are packaged, pip-building them on the
   router is painful, and flash/RAM on a home router are tight.  Rewriting
   in C means reimplementing TPAP/SPAKE2+ — out of the question.  The
   router does networking; the daemon runs on a real box (§6).

## 1. Scope

Two front ends, same features: a **CLI** (for the owner, scripts, tab
completion on room names) and a **web UI** (for the family).  Neither is
second-class; both go through the same HTTP API or the same in-process
engine.

In: start a run (rooms + mode), stop / send home, status, battery/base
warnings, event log, map with the last track, the home rules below,
notifications.  One robot.

Out: schedules, consumables, settings, map editing, multi-robot.  The
family keeps the Tapo app for those.

## 2. Components

One repository, one Python package, three entry points.  Split into
"generic library" and "our home" later only if someone else wants the
library; config already carries everything home-specific.

```
opentapovac/
  robot.py      thin async wrapper over python-kasa: connect, query,
                error-checked send (-3002 and empty replies become exceptions)
  payloads.py   THE flag -> payload table (mode, suction, water, passes)
  rooms.py      id <-> name <-> alias resolution, rooms cache
  plan.py       rooms + mode + home rules -> ordered list of Steps
  monitor.py    pure state machine: (state, observation) -> (state, events,
                actions).  No I/O, fed recorded logs in tests
  engine.py     runs a plan: owns the robot connection, polls, feeds the
                monitor, executes actions, emits events, waits for humans
  events.py     event log (sqlite or jsonl), timestamps in UTC, shown in
                Europe/Oslo
  notify.py     later: ntfy / desktop / email, pluggable
  mapimg.py     map + track rendering (was the tapo-render-map.py prototype)
  web/          small server + one page, no JS build step
  cli.py        opentapovac clean|status|stop|home|rooms|log|serve
```

**Engine is the only thing that talks to the robot.**  The daemon is
"engine + web + HTTP API".  Standalone CLI is "engine in-process": the
same code, blocking until the plan is done, prompts on the terminal
instead of in the browser.

### CLI vs daemon

* `opentapovac clean ...` checks for a daemon (socket/URL in config).  If
  found: submit, print the job id, return.  `--wait` follows the event
  stream until the job ends.  If not found: run standalone, blocking
  (it has to — nothing else will watch the robot).
* Two engines against one robot must not happen: the daemon holds a lock;
  standalone refuses to start if the daemon answers.
* This answers the open question in the old spec: blocking only in
  standalone, or on request.

## 3. Plans, steps and home rules

A job is a list of **Steps**, built by `plan.py` from the request and the
config:

* `Run(rooms=[...], per-room settings)` — one `runCleanTask`.
* `HumanCarry(to=room)` — ask a human to carry the robot, wait for "done".
* `VerifyPosition(room)` — after relocation, check `real_vac_coor` is
  inside the room's pixels in `getMapData`.  Fail → pause + ask.
* `Home()` — `setSwitchCharge`, with the recovery policy (§4).

Rules, all from config:

* `order.first: [bedroom 1, outer hall]`, `order.last: [kjøkken]`.
  Sorted into one multi-room run where possible (fewer trips home, fewer
  homing crossings — see the doorstep notes in the old spec).
* `carry_in` rooms (bedroom 2): must be carried *in*; own Run, preceded
  by HumanCarry + VerifyPosition.  `carry_out` rooms (living room): the
  robot gets in, but must be carried out before going home — monitor pauses
  when the robot wants the dock (`recharge_status` 1 / status 4) and asks.
  A room can be both.  Not yet mapped: the bathroom and the attic,
  probably carry rooms as well.
* Stairs (3) refused without `--force`.
* Vac-then-mop across several rooms: in the one such run so far
  (bedroom 1 + kitchen, 2026-09-25, same `clean_type` 3 for both) the
  robot vacuumed both rooms, then mopped both.  Mixed `clean_type`s per
  room are robot test 2 in the old spec, still to do.

Human waits have a timeout (config).  On timeout the job is left paused
and a notification says so; it never proceeds on its own.

## 4. Monitor and recovery

The monitor is the old spec's monitor loop, plus relocation, as one state
machine.  Observations: `getVacStatus`, `getCleanStatus`, `getBatteryInfo`,
`getBaseStatus`, position.  Actions it may return: `pause`, `resume`,
`home`, `stop`, `ask_human(msg, choices)`, `notify(level, msg)`.

Dock not found (err 21):

1. Robot **not** in the dock room (position vs polygons): one `Home()`
   retry.  If it fails and waypoints are configured and `gotoPoint` is
   verified: go to each waypoint (`hall`, then `kjøkken`), then `Home()`.
   Otherwise ask a human.
2. Robot **in** the dock room: ask a human — "check the dock's power
   (shared socket) and chairs in the way", with a "done, retry" button.
3. Interrupted run: record which rooms of the plan were finished (status
   sequence + per-room track from `getPathData`, if that works) and offer
   "queue the rest" as a button.  Not automatic in v1.

Relocation watchdog: as in the old spec (pause, ask, turn-on-the-spot,
give up after N).  Refuse to start on an unlocked map or with
`auto_change_map` on.

## 5. Web UI

One page, phones first.

* Preset buttons from config ("Halls + kitchen + bedroom 1"), then one
  button per room.
* Mode selector: vac / vac+mop / vac then mop / mop.  Default in config.
* Big red "Stop / go home".
* Status box: state in words, battery, water, current job and step.
  When the engine waits for a human: the question and its buttons,
  prominent.
* Log: last N events, Oslo time.
* Map: PNG of the last `getMapData` + `getPathData`, "reload" button.
  The app's cleaned-area overlay needs the cleaned-area data, which we
  may not have — v1 draws the track only.
* Live updates via server-sent events; plain HTML + a little JS
  (htmx or hand-written), no build step.

HTTP API (the CLI uses it too): `POST /jobs`, `GET /jobs/{id}`,
`POST /jobs/{id}/answer`, `POST /stop`, `GET /status`, `GET /events`
(SSE), `GET /map.png`, `POST /map/refresh`.

Framework: aiohttp (python-kasa already depends on it) or Starlette.
Lean: aiohttp, one dependency less.

## 6. Where it runs, networking, auth

The daemon must reach 10.47.128.10:4433.  Options:

| Host | Reach robot | Family reaches UI | Notes |
|---|---|---|---|
| Box at home (a family member's server) | LAN, trivial | home wifi; outside: needs tunnel | best robustness; depends on that box |
| Own server elsewhere (Ubuntu/puppet or NixOS) | WireGuard **from the router to the server** | public HTTPS + auth, no VPN on phones | router dials out, so dynamic IPv6/IP doesn't matter (keepalive) |
| Laptop | WireGuard or home wifi | no | dev/test only |

Lean: develop on the laptop; deploy on the NixOS server (NixOS module in the
repo, nice for open source) with the router as a WireGuard *client* of
that server.  The router branch `wireguard-insecure-wlan` currently makes
the router the server; that changes.  Robot traffic then leaves the house,
encrypted; the robot itself is still only reachable on tcp/4433 from the
tunnel.

Auth: reverse proxy (nginx/caddy) with HTTP basic auth or OIDC in front,
the daemon binds to localhost only.  No accounts in the daemon.  Family
gets a bookmark with one shared password (or one per person, for the log).

## 7. Config

`~/.config/opentapovac/config.yaml` (credentials stay in
`~/.config/tapo/credentials.yaml`, or a path the config names):

```yaml
robot: {host: 10.47.128.10, credentials: ~/.config/tapo/credentials.yaml}
timezone: Europe/Oslo
defaults: {mode: vac_then_mop, suction: 2, water: 2, passes: 1}
rooms:            # keyed by robot room name or id; names come from the robot
  "kjøkken": {aliases: [kitchen], dock: true}
  6: {aliases: [outer hall, ytre gang]}
  "bedroom 2": {carry_in: true}
  "living room": {carry_out: true}
  "stairs": {forbidden: true}
order: {first: ["bedroom 1", 6], last: ["kjøkken"]}
presets:
  - {label: "Halls + kitchen + bedroom 1", rooms: [5, 6, "kjøkken", "bedroom 1"]}
waypoints: {home_route: [hall, kjøkken]}     # used only once gotoPoint works
# notify: [{type: ntfy, topic: ...}]     # later, see §10
human_wait_timeout: 15m
```

## 8. Testing

As in the old spec: pytest, tests before code.  Fixtures: the recorded
status logs (not in this repository; copy the relevant ones into
`tests/fixtures/`, scrubbed), the known-good payloads in
`payloads/`, a `getMapData` dump for polygon tests.  A fake
robot (scripted observations) drives the engine end to end, so the web UI
and CLI can be developed without the robot.  Robot tests 1–7 in the old
spec still gate the features that depend on them.

## 9. Milestones

1. Library + standalone CLI (`clean`, `status`, `stop`, `rooms`), monitor
   for the simple cases.  = the old "tapo-clean" spec.  Retires
   `tapo-run-queue.sh`.
2. Daemon + HTTP API + CLI-via-daemon.  Messages on the page only.
3. Web UI, map image.
4. Home rules: ordering, carry-rooms, position check.
5. Recovery with waypoints (after `gotoPoint` is verified).
6. Notifications, deployment (NixOS module / puppet), router WireGuard
   as client.  Host not chosen yet.

State 2026-09-26: 1–4 are written and tested against a scripted robot and
the recorded 2026-09-24 evening log; read-only calls (status, rooms, map)
were checked against the real robot.  No run has been sent by it yet.
Where the build differs from the text above:

* **Idle** (safe to send the next run) is 16, or 5/6 held for `settle`
  (60 s), not "5/6 seen twice": in the evening log the robot sat at 5 for
  ~30 s after coming home and then went on to wash the mop.  Standby (0)
  without an error, held as long, also counts, with a warning — the robot
  reported standby on the dock at 100 % on 2026-09-26.
* **Err 21 is not the end of the run**: the robot went home by itself
  ~90 s later both times it happened.  Standby during a run counts as
  given up only after `gave_up_after` (300 s).
* **Stop** is the app's stop payload (protocol.md); the robot heads home
  by itself afterwards.  **Home** (`setSwitchCharge`) is from the app and
  not yet sent; the web page has no button for it.
* The web page selects rooms and then starts; the presets start at once.
* **Carry rooms go first**: each gets a run of its own, before the
  multi-room run, since whoever pressed the button is most likely still
  around.
* **No pause**: `setRobotPause` is untried, so nothing is paused, and the
  engine keeps watching the robot while a question is open (standby then
  doesn't count as giving up).  A carry-in run is sent from the dock like
  any other, so the mops go on first; the question comes when the robot
  leaves the base, and again each time it leaves it mid-run (after a mop
  wash, say).  The robot stops at the doorstep by itself (2026-09-25).  A
  carry-in room asked for later than first gets a warning.  In a
  `carry_out` run the question comes once per trip home (status 4, or
  `recharge_status` 1 away from the base).  Seeing the robot lifted
  (err 4) and put down answers the question as "done"; "skip" (carry-in
  only) stops that run and goes on with the next.
* **VerifyPosition is not a step of its own**: `real_vac_coor` reads
  (0, 0) while docked, so the check is made during the run, once the robot
  has been cleaning, not relocating, for `verify_after` (60 s) after the
  carry.  Any pixel of the room within 15 cm of the position passes;
  another room's pixels stop the run (the app's stop payload) and fail the
  job; no room at all (wall, (0, 0)) is only a warning.
* **Human wait timeout** raises an alert and keeps waiting; the job is
  "waiting" until answered or stopped.  `opentapovac answer`, the web
  page, and the terminal of a standalone or `--wait` clean all answer.
* Extra endpoints: `GET /ping`, `GET /rooms`, `POST /rooms/refresh`.
* **Track and clean records are kept** (`tracks.py`): the robot's track
  was cleared mid-run on 2026-09-26 (14 points left of a 25-minute run),
  so every status poll also fetches the new track points (`getPathData`
  from `start_pos`, which returns only the points from there on).  After
  each run, `getCleanRecords` is read and new records go to
  `clean-records.jsonl` and the log.  Outside jobs the daemon checks the
  status every `watch_interval` (60 s) and records runs from the app the
  same way.

## 10. Decisions (2026-09-26)

* **Own daemon**, not Home Assistant — but keep a later move to HA cheap:
  `robot`, `rooms`, `plan`, `monitor` and `engine` must not import
  anything from `web/` or `cli.py`, and must not assume they own the
  event loop.  An HA custom integration could then wrap the engine, or
  HA could just drive the daemon's HTTP API.
* **Name:** OpenTapoVac (package `opentapovac`).  `tapovac` was too
  generic and sounded official.  The README says up front that this is
  not open firmware.
* **License:** AGPL-3.0-or-later.  Compatible with python-kasa's
  GPL-3.0-or-later.
* **Host:** later.  Development and testing from the laptop, over the
  home wifi or WireGuard.  §6 stays as background.
* **Notifications:** later.  v1 shows messages on the web page (and in
  the CLI) only; `notify.py` waits.
* **python-kasa:** the local editable install of `<python-kasa checkout>`
  (`pip install -e`), branch `tpap-rv50-tls-fix`.  State on 2026-09-26:
  upstream is active (commits weekly, last 2026-09-18) but has not
  released since 0.10.2 (2025-02).  TPAP is ZeliardM's PR
  <https://github.com/python-kasa/python-kasa/pull/1592>: open, mergeable,
  review required, updated 2026-09-24.  Our branch is that PR plus one
  commit, e3338d0 ("Keep TPAP on https if in-band discover says
  tls=0"), which is on the tobixen fork but not offered to the PR yet.
  v1 needs no further library changes: rooms, maps and `runCleanTask`
  go through raw queries.  Room cleaning in python-kasa's `Clean` module
  is a possible later upstream contribution.
* **Ramp:** three months away at least, maybe never.  So the carry flow
  in §3 is real work, not a stop-gap: milestone 4 stays.
