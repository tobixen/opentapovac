# OpenTapoVac

Disclaimer: parts of this file is AI-generated (Claude Opus 5.5), but all of it has been reviewed by a human.

Local control of a TP-Link Tapo robot vacuum (developed against the
RV50 Pro Omni): a daemon, a command-line tool and a small web page for
day-to-day cleaning, with house-specific rules kept in a config file.

## Background

I was not doing my research properly, and ended up with the RV50 Pro
Omni robot vacuum.  I learned some few things about this device:

* The robot itself is quite decent.
* The software does not work out very well in my home.  The firmware seems to be quite buggy, it's frequently doing weird things, particularly it has the tendency to not find the way back to the dock - sometimes searching the whole house without success even if the dock is very visible on the map.  It's also aborting the whole run (i.e. "wash and then mop all the rooms") mid-way without any way to resume if anyhting goes wrong.  It frequently needs to look around to "find my position", and it's not reliably finding the position.
* The firmware is locked down - not possible to fix it, hack it, nor replace it with Valetudo - we're stuck with the vendor-provided firmware.  Hence, the "Open"-part of this project is limited to the client-side software, this is not open firmware.
* The robot has problems with doorsteps.
* It is possible to reverse-engineer the cellphone app, and it is possible to control the device from the local network.  (see also [python-kasa PR #1592](https://github.com/python-kasa/python-kasa/pull/1592)).
* Apparently, if only using the robot in the late nights and early mornings, it will never empty the dust container - and it seems that the water container in the robot won't be refilled.  Its clock was an hour off (UTC+1 while Oslo was on summer time), which pushed the quiet hours into the morning - whether it doesn't handle DST or just lost the update when its internet access was closed is not known.

This project has some few goals:

* Make workarounds for many of the issues found in the software
* Make a CLI for me
* Make a web interface for my family
* Leave enough documentation in the project that the AI can do everything that can be done from the app, if not more.

## Status

"It seems to work" with my 'bot and my home.  It has a daemon, a command-line tool and a web-page.  There are still some planned features that are missing.  In the start I had the idea that it was no point trying to replace the app - just make an interface suitable for daily routines and for working around all the problems encountered.  However, the app stopped working the moment I closed the egress for the vacuum, and I do find it a lot easier to do things from the laptop than from a cellphone app - so the long-term design goal now is to make the cellphone app completely obsolete.

## Installation

From PyPI:

```
pipx install opentapovac     # or: uv tool install opentapovac
```

From a checkout:

```
make install
```

(This auto-detects `uv`, `pipx`, or `pip` and does the right thing.)
Tab completion for bash and zsh comes with it; bash needs the
`bash-completion` package.

TPAP support is not in a python-kasa release yet, so the package carries
its own copy of the TPAP transport from the python-kasa PR branch
(`src/opentapovac/_tpap.py`, see docs/design.md, "Decisions") and runs it
on a released python-kasa.

### NixOS

`nix/package.nix` builds the package and `nix/module.nix` runs the daemon as `services.opentapovac`:

```nix
imports = [ "${opentapovac-src}/nix/module.nix" ];
services.opentapovac = {
  enable = true;
  credentialsFile = "/etc/opentapovac/credentials.yaml";  # user: / pass:
  settings = { robot.host = "192.0.2.10"; timezone = "Europe/Oslo"; };
};
```

`settings` is the config file below, as Nix.  The daemon binds to
localhost; put nginx with authentication in front of it.

## Configuration

`~/.config/opentapovac/config.yaml`; the account password stays in
`~/.config/tapo/credentials.yaml` (`user:` and `pass:`, mode 0600), or in
`$KASA_USERNAME`/`$KASA_PASSWORD`.  A minimal config:

```yaml
robot: {host: 192.0.2.10}
timezone: Europe/Oslo
defaults: {mode: vac_then_mop, suction: 2, water: 2, passes: 1}
rooms:                       # keyed by the robot's room name or id
  "kjøkken": {aliases: [kitchen]}
  6: {aliases: [outer hall]}
  stairs: {forbidden: true}  # refused without --force
  "bedroom 2": {carry_in: true}    # can't get in by itself
  "living room": {carry_out: true} # can't get out by itself
  "boys room": {home_route: [kjøkken]}  # lost here: this route instead
order: {first: ["bedroom 1", 6], last: ["kjøkken"]}  # within a run
presets:
  - {label: "Halls + kitchen", rooms: [5, 6, "kjøkken"]}
waypoints: {home_route: [hall, kjøkken]}  # lost on the way home: via these, then home
waypoint_timeout: 3m         # a waypoint not reached in this long: an alert
listen: 127.0.0.1:8765       # the daemon
human_wait_timeout: 15m      # then an alert; it keeps waiting
```

Room names come from the robot; `opentapovac rooms --refresh` reads them.
The poll interval and the monitor's timeouts can be set under `monitor:`
(see `src/opentapovac/config.py`).

## Usage

Run `opentapovac --help`, or `opentapovac COMMAND --help`, for the full
list of options.

```
opentapovac clean kitchen "outer hall"      # one run, rooms in this order
opentapovac clean --mop --sequential hall 6 # one run per room
opentapovac status | stop | home | rooms | log
opentapovac answer done                     # "carry the robot into ..."
opentapovac map map.png                     # map with the last 12 h of tracks
opentapovac goto hall | goto 4100 3000      # send it to a room or a point (mm)
opentapovac pause | resume
opentapovac serve                           # the daemon and the web page
```

Without a daemon, `clean` blocks until the robot is back on the dock and
reports what happens on the way (stuck, dock not found, relocation, empty
water tank).  With a daemon running, every command goes through it and
`clean` returns at once; `--wait` follows the job.  A job started while
another runs is queued and runs in turn; Stop, or a job that fails or is
stopped, drops the queue.

While the robot works, a progress line comes every minute (percent done,
vacuuming or mopping, room, battery), with a warning when a vacuum pass
is skipped or the robot makes no progress for five minutes.  After a
relocation, a "position found" line says which room the robot now
thinks it is in.

When the robot ends a run without its mop pass, the mopping is sent
once more; when it leaves a run unfinished, the job asks whether to send
it again.  Not with the battery low or the water tank empty.
`redo_missed: false` in the config turns this off.

Lost on the way home ("dock not found"), the robot is guided room by
room along the configured route (`waypoints: {home_route: [...]}`, or a
room's own `home_route`), then sent home.  `goto` does the same by hand:
it sends the robot to a floor spot in a room, or to a point, outside the
no-go zones.  Like Stop and Home, it ends the current job.

## The web page

`opentapovac serve` also serves a web page for day-to-day runs: buttons
for the rooms and the presets, the mode, Stop and "Return to dock", the
status, the event log, questions to answer, and the map.  Tapping the map
sends the robot there.  The map shows the tracks of the last 12 hours,
mopping in its own colour, with checkboxes for vacuuming, mopping and
movement.  The version is shown next to the title.

The robot keeps only a short track and clears it now and then, so the
daemon fetches it while a run goes on, also for runs started from the
app, and keeps it in `~/.local/state/opentapovac/tracks/`, with the robot's
own clean records (time, area, mop washes) in `clean-records.jsonl` next
to it.  The map shows the recorded tracks of the last 12 hours
(`opentapovac map --max-age HOURS`).

The daemon binds to localhost.  To reach the web page from phones, put a
reverse proxy with authentication in front of it; the daemon has no
accounts of its own.  Name the proxy's host in `allowed_hosts:` in the
config: the daemon answers only to localhost and the names listed there,
and takes POSTs only as JSON from its own origin, so other web pages can't
drive the robot through your browser.

A carry room gets a run of its own, before the others.  For a
`carry_in` room the run starts from the dock as usual (so the mops go on
first), and the job asks someone to carry the robot in when it leaves
the base; the robot waits at the doorstep.  A minute after it is put
down the job checks that the robot knows where it is, and stops it if
it thinks it is in another room.  "skip" drops the room and goes on with
the rest.  For a `carry_out` room it asks each time the robot heads for
the dock.  Lifting the robot and putting it down counts as "done".  The question shows on the web page, on the
terminal of a standalone or `--wait` clean, and in `opentapovac status`;
the job waits until someone answers or stops it.  Meanwhile the robot is
paused, so it doesn't give up the run at the doorstep, and resumed once
carried (`pause_for_carry: false` turns this off).

Before a run the robot's map must be locked and "auto change map" off,
since a bad relocation can otherwise overwrite the map; `--force` skips
that check.

## Are you using this?  Please say so

The maintainers interest for this project will likely die the day the robot breaks down.  If you use OpenTapoVac, or would like to, please open an issue or a discussion and say which robot you have.  Knowing there are other users matters, and at some point someone else may need to take over as maintainer.

## License

AGPL-3.0-or-later.  See [LICENSE](LICENSE).
